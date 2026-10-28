"""Money and access fault cases against a shared RPC double, NOT PostgreSQL proof."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from unittest import TestCase
from unittest.mock import Mock

from collectors.intelligence_pipeline.contracts import Usage
from collectors.intelligence_pipeline.costing import MODEL, PRICE_VERSION, micro_usd, paid_cap
from collectors.intelligence_pipeline.gemini import (FreeApproval, PaidApproval, GeminiModel,
                                                    ModelAccessStop, access_approval)
from collectors.intelligence_pipeline.postgres import PostgresQuota
from collectors.intelligence_pipeline.ports import QuotaExceeded
from test_adapters import NOW
import test_adapters as fixtures

KEY = 'synthetic-key'


def approval(mode='CAPPED_PAID'):
    fields = dict(account_scope='one-reviewed-run', reference='SYNTHETIC-ONLY-APPROVAL',
                  key_sha256=hashlib.sha256(KEY.encode()).hexdigest(), expires_at='2026-10-20T00:00:00Z')
    return PaidApproval(**fields, cap_micro_usd=1000000) if mode == 'CAPPED_PAID' else FreeApproval(**fields)


class BudgetAPI:
    """Central fixture state survives recreation of Python adapters; never real SQL."""
    def __init__(self, access):
        self.rows = {}; self.calls = []
        self.config = dict(contract_version='exp009-runtime-input29-v3', scope=access.account_scope,
            approval_ref=access.reference, access_mode=access.mode, key_sha256=access.key_sha256,
            model=MODEL, price_version=PRICE_VERSION, cap_micro_usd=getattr(access,'cap_micro_usd',0),
            expires_at=access.expires_at, enabled=True, frozen=False, max_requests=15)

    def call(self, operation, args, *, readonly=False):
        self.calls.append((operation, deepcopy(args), readonly))
        if args['scope'] != self.config['scope']: raise ValueError('WRONG_SCOPE')
        if operation == 'limits': return dict(self.config, charged_micro_usd=sum(r['charged'] for r in self.rows.values()))
        rid = args['request_id']
        if operation == 'reserve':
            money = micro_usd(Usage(**args['estimate']))
            if args['micro_usd'] != money: raise ValueError('PRICE_MISMATCH')
            if (self.config['frozen'] or rid in self.rows or len(self.rows)>=self.config['max_requests'] or
                (self.config['access_mode']=='CAPPED_PAID' and
                 sum(r['charged'] for r in self.rows.values())+money>self.config['cap_micro_usd'])):
                raise QuotaExceeded('TOTAL_SCOPE_LIMIT')
            self.rows[rid] = dict(state='RESERVED', reserved=money, charged=money,
                                  estimate=Usage(**args['estimate']))
        elif operation == 'dispatch':
            if self.rows[rid]['state'] != 'RESERVED': raise ValueError('NO_DISPATCH_RETRY')
            self.rows[rid]['state'] = 'DISPATCHED'
        elif operation == 'unknown':
            self.rows[rid]['state'] = 'UNKNOWN'
        elif operation == 'settle':
            row = self.rows[rid]; value = Usage(**args['actual'])
            if row['state'] != 'DISPATCHED': raise ValueError('NO_SETTLE_REPLAY')
            if args['micro_usd'] != micro_usd(value): raise ValueError('PRICE_MISMATCH')
            row.update(state='SETTLED', charged=micro_usd(value))
            if not value.fits(row['estimate']) or row['charged'] > row['reserved']:
                self.config['frozen'] = True  # Double models persisted freeze before raised result.
                raise QuotaExceeded('PERSISTED_FREEZE')
        else: raise AssertionError('UNEXPECTED_RPC')


class CostingTests(TestCase):
    def test_per_call_ceiling_and_thought_output_price(self):
        self.assertEqual(micro_usd(Usage(1,0,1)),1)
        self.assertEqual(micro_usd(Usage(0,1,1)),3)
        self.assertEqual(micro_usd(Usage(100,14,1)),65)
        self.assertEqual(micro_usd(Usage(1048576,6000,1)),329573)
        for invalid in (0,True,-1,1000001,1.0):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):paid_cap(invalid)

    def test_recreated_adapters_and_unknowns_share_total_without_reset(self):
        access=approval();api=BudgetAPI(access);maximum=Usage(1048576,6000,1)
        for i in range(3):
            q=PostgresQuota(api,access.account_scope,access)
            q.reserve(str(i),NOW,maximum);q.dispatch(str(i));q.unknown(str(i))
        self.assertEqual(sum(r['charged'] for r in api.rows.values()),988719)
        q=PostgresQuota(api,access.account_scope,access)
        with self.assertRaises(QuotaExceeded):q.reserve('fourth',NOW,maximum)
        self.assertEqual(len(api.rows),3)
        self.assertTrue(all(r['state']=='UNKNOWN' for r in api.rows.values()))

    def test_actual_usage_releases_reserve_but_duplicate_never_allocates_again(self):
        access=approval();api=BudgetAPI(access);q=PostgresQuota(api,access.account_scope,access)
        q.reserve('one',NOW,Usage(1048576,6000,1));q.dispatch('one');q.settle('one',Usage(100,14,1))
        self.assertEqual(api.rows['one']['charged'],65)
        with self.assertRaises(QuotaExceeded):q.reserve('one',NOW,Usage(1048576,6000,1))
        PostgresQuota(api,access.account_scope,access).reserve('two',NOW,Usage(1048576,6000,1))

    def test_over_reserve_persists_freeze_across_adapter_recreation(self):
        access=approval();api=BudgetAPI(access);q=PostgresQuota(api,access.account_scope,access)
        q.reserve('one',NOW,Usage(1048576,6000,1));q.dispatch('one')
        with self.assertRaises(QuotaExceeded):q.settle('one',Usage(1048577,6000,1))
        self.assertTrue(api.config['frozen'])
        with self.assertRaises(QuotaExceeded):PostgresQuota(api,access.account_scope,access).verify()

    def test_persisted_mode_key_price_cap_expiry_or_contract_mismatch_fails_before_reserve(self):
        for field,value in [('access_mode','FREE_ONLY'),('key_sha256','0'*64),('price_version','future'),
                            ('cap_micro_usd',999999),('expires_at','2026-11-01T00:00:00Z'),
                            ('contract_version','old'),('approval_ref','other')]:
            access=approval();api=BudgetAPI(access);api.config[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                PostgresQuota(api,access.account_scope,access).reserve('one',NOW,Usage(1048576,6000,1))
            self.assertEqual([op for op,a,ro in api.calls],['limits'])

    def test_no_approval_is_not_an_implicit_paid_scope(self):
        api=Mock()
        with self.assertRaises(ValueError):PostgresQuota(api,'scope').reserve('one',NOW,Usage(10,20,1))
        api.call.assert_not_called()

    def test_free_scope_keeps_explicit_zero_money_cap_and_no_paid_fallback(self):
        access=approval('FREE_ONLY');api=BudgetAPI(access)
        self.assertEqual(PostgresQuota(api,access.account_scope,access).verify()['cap_micro_usd'],0)
        value=asdict(access);value['cap_micro_usd']=1000000
        with self.assertRaises(ValueError):access_approval(value)


class AccessFaultTests(TestCase):
    def test_unapproved_key_expired_or_disabled_modes_make_zero_http_calls(self):
        for access in (approval(),approval('FREE_ONLY')):
            for kind in ('key','expiry','disabled'):
                send=Mock();m=GeminiModel(KEY,access,enabled=True,transport=send,clock=lambda:NOW)
                if kind=='key':m.key='other'
                if kind=='expiry':m.clock=lambda:NOW.replace(year=2027)
                if kind=='disabled':m.enabled=False
                with self.subTest(mode=access.mode,kind=kind),self.assertRaises(ModelAccessStop):
                    m.estimate('extract',fixtures.GeminiTests().payload())
                send.assert_not_called()

    def test_paid_structured_request_uses_dossier_cap_and_provider_metadata_is_checked(self):
        fixture=fixtures.GeminiTests();p=fixture.payload();good=fixture.reply()
        for mutation in ('model','tier','total','bool','missing_thoughts','limit','tool','finish'):
            r=deepcopy(good)
            if mutation=='model':r['modelVersion']='other'
            if mutation=='tier':r['serviceTier']='flex'
            if mutation=='total':r['usageMetadata']['totalTokenCount']=34
            if mutation=='bool':r['usageMetadata']['promptTokenCount']=True
            if mutation=='missing_thoughts':del r['usageMetadata']['thoughtsTokenCount']
            if mutation=='limit':r['usageMetadata'].update(candidatesTokenCount=6001,totalTokenCount=6014)
            if mutation=='tool':r['candidates'][0]['content']['parts']=[{'functionCall':{'name':'x'}}]
            if mutation=='finish':r['candidates'][0]['finishReason']='MAX_TOKENS'
            send=Mock(return_value=r);m=GeminiModel(KEY,approval(),enabled=True,transport=send,clock=lambda:NOW)
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):m.execute('extract',p,'one',m.estimate('extract',p))
            send.assert_called_once()
