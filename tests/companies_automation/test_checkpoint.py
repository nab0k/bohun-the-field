from dataclasses import replace
from datetime import timedelta
import unittest

from collectors.companies.automation import contracts as c, checkpoint as w
from collectors.companies.automation.changes import evaluate
from companies_automation.test_changes import CID, NOW, POLICY, website_claim


class CheckpointTests(unittest.TestCase):
    def test_changes_roundtrip_keeps_exact_hash_and_sources(self):
        ch = evaluate(c.CompanyView(CID, country='DE'), (website_claim(),), section='IDENTITY', policy=POLICY, now=NOW)
        other = w.decode(w.encode(ch), type(ch))
        self.assertEqual(other, ch)
        self.assertEqual(other.fingerprint, ch.fingerprint)
        w.validate_changes(other, POLICY, NOW)

    def test_job_lease_policy_and_view_roundtrip(self):
        objects = [POLICY, c.JobSpec('ENRICH_SECTION', POLICY.fingerprint, POLICY.version, c.digest('input'), CID, 'PRODUCTS'),
                   c.CompanyView(CID, country='UA', fields=(('/identity/country',c.FieldValue(True,'"UA"','manual')),)),
                   c.SectionState('HAS_HOLD_CLAIMS', NOW+timedelta(days=7), c.digest('old'))]
        objects.append(c.Lease(objects[1], 'synthetic-lease', NOW+timedelta(seconds=300), 1))
        for obj in objects:
            with self.subTest(obj=type(obj).__name__):
                self.assertEqual(w.decode(w.encode(obj),type(obj)), obj)

    def test_checkpoint_roundtrip_sets_maps_and_foreign_opaque_key(self):
        cp=w.empty_checkpoint();cp['existing_keys']=frozenset([c.digest('foreign'),c.digest('one')])
        cp['seen_triggers']=frozenset([c.digest('trigger')])
        cp['section_inputs'][(CID,'EVENTS')]=c.digest('event')
        cp['profile_hashes'][(CID,'EVENTS')]=c.digest('profile')
        cp['section_states'][(CID,'EVENTS')]=c.SectionState()
        self.assertEqual(w.decode_checkpoint(w.encode_checkpoint(cp)),cp)

    def test_duplicate_scope_or_key_denied(self):
        raw=w.encode_checkpoint(w.empty_checkpoint())
        raw['section_inputs']=[{'company_id':CID,'section':'EVENTS','value':c.digest('e')}]*2
        with self.assertRaisesRegex(c.ContractError,'DUPLICATE_SCOPE'):w.decode_checkpoint(raw)
        raw=w.encode_checkpoint(w.empty_checkpoint());raw['existing_keys']=[c.digest('a')]*2
        with self.assertRaises(c.ContractError):w.decode_checkpoint(raw)

    def test_extra_missing_fields_unknown_tags_and_wrong_types(self):
        good=w.encode(POLICY)
        for raw in (good|{'extra':True}, {k:v for k,v in good.items() if k!='version'},good|{'_type':'eval'},good|{'max_claims':True}):
            with self.subTest(raw=raw),self.assertRaises(c.ContractError):w.decode(raw)

    def test_naive_and_non_utc_wire_times_denied(self):
        raw=w.encode(c.SectionState('HAS_HOLD_CLAIMS',NOW,c.digest('x')))
        for time in ('2026-11-02T12:00:00','2026-11-02T12:00:00+01:00','bad'):
            with self.subTest(time=time),self.assertRaises(c.ContractError):w.decode(raw|{'next_due':time})

    def test_byte_depth_and_float_limits(self):
        with self.assertRaises(c.ContractError):w.encode({'large':'x'*(w.MAX_BYTES+1)})
        v='leaf'
        for _ in range(30):v=[v]
        with self.assertRaises(c.ContractError):w.encode(v)
        with self.assertRaises(c.ContractError):w.decode(1.5)
        with self.assertRaises(c.ContractError):w.encode(float('nan'))

    def test_checkpoint_limits_and_foreign_structured_scope(self):
        cp=w.empty_checkpoint();cp['seen_triggers']=frozenset(c.digest(i) for i in range(501))
        with self.assertRaises(c.ContractError):w.encode_checkpoint(cp)
        cp=w.empty_checkpoint();cp['profile_hashes'][('other','EVENTS')]=c.digest('x')
        with self.assertRaises(c.ContractError):w.scope_checkpoint(cp,{CID})

    def test_changes_reject_scope_qualifier_value_or_public_escalation(self):
        ch=evaluate(c.CompanyView(CID,country='DE'),(website_claim(),),section='IDENTITY',policy=POLICY,now=NOW)
        p=ch.claims[0].proposal
        for bad in (replace(ch,policy_hash=c.digest('foreign')),replace(ch,reviewed_at=None),
                    replace(ch,claims=(replace(ch.claims[0],proposal=replace(p,value_json='"different"')),))):
            with self.subTest(bad=bad),self.assertRaises(c.ContractError):w.validate_changes(bad,POLICY,NOW)
        raw=w.encode(ch);raw['claims'][0]['public_release']=True
        with self.assertRaises(c.ContractError):w.decode(raw)
