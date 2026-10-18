"""Offline contracts/fault injection ONLY: fakes do not prove SQL/RLS/locking."""
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from collectors.inoreader import intake, intake_contract as contract, sync
from collectors.inoreader.intake_store import (CommitUnknown, PostCommitUnconfirmed,
                                                PostgresIntakeStore)

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
SHA = "a" * 40
BATCH_ID = "17e7da26-8421-4dde-92ef-eb4ad64045a0"


def item(key="one", body="<p>PRIVATE_BODY_SENTINEL — Україна</p>", now=NOW):
    raw = {"id": key, "title": "PRIVATE_TITLE_SENTINEL", "published": 1792324800,
           "canonical": [{"href": "https://private.example.test/news"}],
           "origin": {"title": "Publisher", "streamId": "feed/example"}}
    if body is not None:
        raw["summary"] = {"content": body}
    return sync.normalize(raw, "BOHUN_Test", captured_at=now)


def batch(items=None, batch_id=BATCH_ID, **kwargs):
    return contract.prepare_batch([item()] if items is None else items, batch_id=batch_id,
                                   producer_sha=SHA, pipeline_version="intake-v1",
                                   collected_at=NOW, **kwargs)


def alter_manifest(value, **changes):
    manifest = json.loads(value.manifest_json)
    manifest.update(changes)
    text = contract.canonical(manifest)
    return contract.PreparedBatch(text, contract.sha256(text), value.payloads)


class OfflineTest(unittest.TestCase):
    def setUp(self):
        guard = patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network"))
        guard.start()
        self.addCleanup(guard.stop)


class ContractTests(OfflineTest):
    def test_deterministic_unicode_bytes_and_no_input_mutation(self):
        original = item()
        before = copy.deepcopy(original)
        first, second = batch([original]), batch([original])
        self.assertEqual(first, second)
        self.assertEqual(original, before)
        self.assertIn("Україна", first.payloads[0])
        self.assertNotIn("captured_at", first.payloads[0])
        manifest = contract.validate_batch(first)
        self.assertEqual(manifest["payload_bytes"], len(first.payloads[0].encode()))
        self.assertNotEqual(manifest["payload_bytes"], len(first.payloads[0]))
        self.assertEqual(manifest["items"][0]["received_at"], "2026-10-18T12:00:00.000000+00:00")

    def test_receipt_time_does_not_change_artifact_but_source_change_does(self):
        original = batch()
        later = batch([item(now=NOW+timedelta(hours=1))])
        changed = batch([item(body="<p>Changed source</p>")])
        self.assertEqual(original.payloads, later.payloads)
        self.assertNotEqual(original.manifest_sha256, later.manifest_sha256)
        self.assertNotEqual(original.payloads, changed.payloads)

    def test_unknown_completeness_and_capture_hashes_preserved(self):
        original = item()
        stored = json.loads(batch([original]).payloads[0])["item"]["raw_data"]["content_capture"]
        self.assertEqual(stored, {k:v for k,v in original["raw_data"]["content_capture"].items()
                                  if k != "captured_at"})
        self.assertEqual(stored["completeness"], "UNKNOWN")

    def test_bodyless_and_empty_html_are_explicit_not_analysis_ready(self):
        for body, status in [(None,"METADATA_ONLY"),("<script>secret</script>","EMPTY_TEXT")]:
            with self.subTest(body=body):
                prepared = batch([item(body=body)])
                self.assertEqual(json.loads(prepared.payloads[0])["item"]["raw_data"]["content_capture"]["status"], status)
                self.assertEqual(intake.summary(prepared)["body_items"], 0)
        empty = batch([])
        self.assertEqual(contract.validate_batch(empty)["outcome"], "EMPTY")
        self.assertEqual(intake.summary(empty)["result"], "INCONCLUSIVE_NO_BODY")

    def test_duplicate_and_six_items_rejected(self):
        for items in ([item(),item()], [item(str(i)) for i in range(6)]):
            with self.assertRaises(contract.IntakeError):
                batch(items)

    def test_naive_time_invalid_sha_and_link_ids_rejected(self):
        for mutation in ({"collected_at":"2026-10-18T12:00:00"},
                         {"producer_sha":"main"}, {"pipeline_version":""},
                         {"batch_id":"not-an-id"}, {"payload_bytes":False}):
            with self.subTest(mutation=mutation), self.assertRaises(contract.IntakeError):
                contract.validate_batch(alter_manifest(batch(), **mutation))
        for invalid in (True,-1,"1",2**63):
            with self.subTest(link=invalid), self.assertRaises(contract.IntakeError):
                batch(existing_item_ids={"one":invalid})

    def test_hash_tamper_malformed_json_and_duplicate_keys_rejected(self):
        prepared = batch()
        with self.assertRaises(contract.IntakeError):
            contract.validate_batch(contract.PreparedBatch(prepared.manifest_json,"0"*64,prepared.payloads))
        with self.assertRaises(contract.IntakeError):
            contract.validate_batch(contract.PreparedBatch(prepared.manifest_json,prepared.manifest_sha256,
                                                             (prepared.payloads[0].replace("Україна","Ukraine"),)))
        for text in ('{"a":1,"a":2}', '{"a": 1}', 'NaN', '{', '"\\u0000"', '"\\ud800"'):
            with self.subTest(text=text), self.assertRaises(contract.IntakeError):
                contract.parse(text)

    def test_forged_body_hash_text_and_qa_fields_rejected(self):
        for mutation in (lambda c:c.update(text="forged"),
                         lambda c:c.update(raw_html_sha256="0"*64),
                         lambda c:c.update(completeness="COMPLETE"),
                         lambda c:c.update(public_release=True),
                         lambda c:c.update(text_bytes=False)):
            altered = item()
            mutation(altered["raw_data"]["content_capture"])
            with self.assertRaises(contract.IntakeError):
                batch([altered])

    def test_nonfinite_unsupported_metadata_and_nul_rejected_without_str_coercion(self):
        for value in (float("nan"), float("inf"), object(), "\x00", "\ud800"):
            altered = item()
            altered["raw_data"]["origin_title"] = value
            with self.subTest(value=type(value).__name__), self.assertRaises(contract.IntakeError):
                batch([altered])

    def test_oversize_and_evicted_body_statuses_remain_explicit(self):
        from collectors.inoreader.content_contract import CaptureBudget
        raw = {"id":"one","title":"Title","summary":{"content":"<p>"+"x"*1500+"</p>"}}
        evicted = sync.normalize(raw,"BOHUN_Test",captured_at=NOW,content_budget=CaptureBudget(limit=650))
        self.assertEqual(evicted["raw_data"]["content_capture"]["status"],"CAPTURE_BUDGET_EXHAUSTED")
        contract.validate_batch(batch([evicted]))
        oversize = item(body="x" * (128*1024+1))
        self.assertEqual(oversize["raw_data"]["content_capture"]["status"],"TOO_LARGE")
        contract.validate_batch(batch([oversize]))
        oversize["raw_data"]["content_capture"]["issues"] = ["FAKE_REASON"]
        with self.assertRaises(contract.IntakeError):
            batch([oversize])

    def test_whole_payload_and_capture_budgets_rejected_not_truncated(self):
        altered = item()
        altered["raw_data"]["origin_title"] = "x" * contract.MAX_PAYLOAD_BYTES
        with self.assertRaises(contract.IntakeError):
            batch([altered])
        # Many small tags retain a small text but large HTML; cumulative >5 MiB.
        html = "<i></i>" * 170000 + "<p>retained</p>"
        items = [item(str(i), html) for i in range(5)]
        with self.assertRaises(contract.IntakeError):
            batch(items)
        self.assertIsNotNone(items[0]["raw_data"]["content_capture"]["raw_html"])


class FakeDB:
    """Transactional protocol double, deliberately NOT a PostgreSQL simulator."""
    def __init__(self):
        self.data = {"batches":{},"artifacts":{},"identities":{},"jobs":{}}
        self.connections = []
        self.lose_ack = False
        self.fail_member = None
        self.fail_next_connection = False
        self.corrupt_read = False
        self.role = "bohun_intake"
        self.autocommit = False

    def connect(self):
        if self.fail_next_connection:
            self.fail_next_connection = False
            raise OSError("PRIVATE_ERROR_SENTINEL")
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.pending = copy.deepcopy(db.data)
        self.autocommit = db.autocommit
        self.queries = []
        self.commits = 0
        self.closed = False
        self.row = None

    def execute(self, query, params=None):
        self.queries.append((query,params))
        self.row = None
        if query.startswith("SET "):
            return self
        if query == "SELECT current_user, session_user":
            self.row = (self.db.role,self.db.role)
        elif "exp009.persist_intake" in query:
            prepared = contract.PreparedBatch(params[0],params[1],tuple(params[2]))
            manifest = contract.validate_batch(prepared)
            old = self.pending["batches"].get(prepared.batch_id)
            if old is not None:
                if old[0] != prepared:
                    raise ValueError("PRIVATE_CONFLICT_SENTINEL")
                self.row = (copy.deepcopy(old[1]),)
                return self
            receipt = dict(batch_id=prepared.batch_id,manifest_json=prepared.manifest_json,
                           manifest_sha256=prepared.manifest_sha256,items=[])
            for i,(member,payload) in enumerate(zip(manifest["items"],prepared.payloads)):
                if self.db.fail_member == i:
                    raise OSError("PRIVATE_LAST_MEMBER_FAILURE")
                identity = (member["external_id"],member["payload_sha256"])
                artifact_id = self.pending["identities"].setdefault(identity,str(uuid4()))
                self.pending["artifacts"].setdefault(artifact_id,payload)
                job_id = self.pending["jobs"].setdefault((artifact_id,manifest["pipeline_version"]),str(uuid4()))
                receipt["items"].append(dict(ordinal=i,artifact_id=artifact_id,job_id=job_id,
                                               payload_sha256=member["payload_sha256"]))
            self.pending["batches"][prepared.batch_id] = (prepared,copy.deepcopy(receipt))
            self.row = (receipt,)
        elif "exp009.read_intake" in query:
            stored = self.pending["batches"].get(params[0])
            self.row = (copy.deepcopy(stored[1]) if stored else None,)
            if self.db.corrupt_read and stored:
                self.row[0]["items"][0]["job_id"] = "invalid-id"
        elif "exp009.read_artifact" in query:
            payload = self.pending["artifacts"].get(params[0])
            self.row = (payload if payload and contract.sha256(payload) == params[1] else None,)
        else:
            raise AssertionError("Unexpected SQL")
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        self.commits += 1
        self.db.data = copy.deepcopy(self.pending)
        if self.db.lose_ack:
            self.db.lose_ack = False
            raise OSError("PRIVATE_COMMIT_ACK_SENTINEL")

    def rollback(self):
        self.pending = copy.deepcopy(self.db.data)

    def close(self):
        self.closed = True


class AdapterTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.db = FakeDB()
        self.store = PostgresIntakeStore(self.db.connect)

    def test_success_checks_before_commit_and_fresh_readonly_after_commit(self):
        prepared = batch()
        receipt = self.store.persist(prepared)
        self.assertEqual(receipt["batch_id"], BATCH_ID)
        self.assertEqual(len(self.db.connections),2)
        write,read = self.db.connections
        self.assertEqual(write.commits,1)
        self.assertEqual(read.commits,0)
        self.assertEqual(read.queries[0],("SET TRANSACTION READ ONLY",None))
        self.assertTrue(all(c.closed for c in self.db.connections))
        self.assertEqual(self.store.read(BATCH_ID)[0],prepared)

    def test_invalid_batch_never_connects(self):
        with self.assertRaises(contract.IntakeError):
            self.store.persist(alter_manifest(batch(), item_count=5))
        self.assertEqual(self.db.connections,[])

    def test_full_role_or_autocommit_rejected_before_function(self):
        for role,autocommit in (("postgres",False),("bohun_intake",True)):
            self.db.role,self.db.autocommit = role,autocommit
            with self.subTest(role=role), self.assertRaises(contract.IntakeError):
                self.store.persist(batch())
            conn = self.db.connections[-1]
            self.assertFalse(any("persist_intake" in q for q,_ in conn.queries))
            self.assertEqual(conn.commits,0)
            self.assertTrue(conn.closed)

    def test_last_member_failure_does_not_commit_or_attempt_postread(self):
        self.db.fail_member = 1
        original = copy.deepcopy(self.db.data)
        with self.assertRaisesRegex(contract.IntakeError,"INTAKE_WRITE_FAILED_NO_RETRY"):
            self.store.persist(batch([item("one"),item("two")]))
        self.assertEqual(self.db.data,original)
        self.assertEqual(len(self.db.connections),1)
        self.assertEqual(self.db.connections[0].commits,0)

    def test_lost_commit_ack_is_unknown_no_reconnect_or_write_retry(self):
        self.db.lose_ack = True
        with self.assertRaises(CommitUnknown):
            self.store.persist(batch())
        self.assertEqual(len(self.db.connections),1)
        self.assertEqual(self.db.connections[0].commits,1)
        self.assertIsNotNone(self.store.read(BATCH_ID))  # Caller explicitly recovers by ID.

    def test_postcommit_connection_failure_keeps_committed_input(self):
        original_commit = FakeConnection.commit
        def commit_then_unavailable(conn):
            original_commit(conn)
            self.db.fail_next_connection = True
        with patch.object(FakeConnection,"commit",commit_then_unavailable):
            with self.assertRaises(PostCommitUnconfirmed):
                self.store.persist(batch())
        self.assertEqual(len(self.db.connections),1)
        self.assertEqual(len(self.db.data["batches"]),1)
        self.assertIsNotNone(self.store.read(BATCH_ID))

    def test_commit_failure_before_effect_is_also_unknown_not_retried(self):
        def unavailable(conn):
            conn.commits += 1
            raise OSError("PRIVATE_FAILURE_BEFORE_COMMIT")
        with patch.object(FakeConnection,"commit",unavailable):
            with self.assertRaises(CommitUnknown):
                self.store.persist(batch())
        self.assertEqual(len(self.db.connections),1)
        self.assertEqual(self.db.data["batches"],{})
        self.assertIsNone(self.store.read(BATCH_ID))

    def test_corrupt_postcommit_receipt_is_unconfirmed_without_rewrite(self):
        original_commit = FakeConnection.commit
        def commit_then_corrupt_read(conn):
            original_commit(conn)
            self.db.corrupt_read = True
        with patch.object(FakeConnection,"commit",commit_then_corrupt_read):
            with self.assertRaises(PostCommitUnconfirmed):
                self.store.persist(batch())
        self.assertEqual(len(self.db.connections),2)
        self.assertEqual(sum(c.commits for c in self.db.connections),1)
        self.assertEqual(len(self.db.data["batches"]),1)

    def test_replay_uses_same_ids_and_new_receipt_reuses_artifact_job(self):
        first = self.store.persist(batch())
        again = self.store.persist(batch())
        self.assertEqual(first,again)
        next_receipt = self.store.persist(batch([item(now=NOW+timedelta(hours=1))],batch_id=str(uuid4())))
        self.assertEqual(first["items"],next_receipt["items"])
        self.assertEqual([len(self.db.data[k]) for k in ("batches","artifacts","jobs")],[2,1,1])

    def test_same_id_different_input_conflicts_without_commit(self):
        self.store.persist(batch())
        before = copy.deepcopy(self.db.data)
        with self.assertRaises(contract.IntakeError):
            self.store.persist(batch([item(body="<p>Changed</p>")]))
        self.assertEqual(self.db.data,before)
        self.assertEqual(self.db.connections[-1].commits,0)

    def test_wrong_receipt_or_body_prevents_commit(self):
        original_execute = FakeConnection.execute
        for corruption in ("receipt","body"):
            def tamper(conn,query,params=None):
                result = original_execute(conn,query,params)
                if corruption == "receipt" and "persist_intake" in query:
                    conn.row[0]["items"][0]["job_id"] = "invalid-id"
                if corruption == "body" and "read_artifact" in query:
                    conn.row = ("PRIVATE_FORGED_BODY",)
                return result
            with self.subTest(corruption=corruption), patch.object(FakeConnection,"execute",tamper):
                with self.assertRaises(contract.IntakeError):
                    self.store.persist(batch())
            self.assertEqual(self.db.connections[-1].commits,0)
            self.assertEqual(len(self.db.data["batches"]),0)

    def test_sql_uses_bound_parameters_not_embedded_payload(self):
        prepared = batch([item("one' ; DROP SCHEMA public; --")])
        self.store.persist(prepared)
        for conn in self.db.connections:
            for query,params in conn.queries:
                self.assertNotIn("PRIVATE_",query)
                self.assertNotIn("DROP SCHEMA",query)


class CLITests(OfflineTest):
    def test_real_module_help_without_credentials_or_driver(self):
        env = {k:v for k,v in os.environ.items() if not any(x in k for x in
               ("INOREADER","SUPABASE","BOHUN_INTAKE","PYTHONPATH"))}
        result = subprocess.run([sys.executable,"-B","-m","collectors.inoreader.intake","--help"],
                                capture_output=True,text=True,env=env,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("--batch-id",result.stdout)

    def test_no_live_flag_fails_before_credentials_or_io(self):
        with patch.object(intake,"open_store",side_effect=AssertionError("Unexpected IO")):
            with self.assertRaisesRegex(contract.IntakeError,"LIVE_ACCESS_NOT_ENABLED"):
                intake.main(["read","--batch-id",BATCH_ID,"--expected-sha",SHA])

    def test_existing_batch_never_fetches_provider_even_if_unavailable(self):
        db = FakeDB(); store = PostgresIntakeStore(db.connect)
        store.persist(batch())
        with patch.object(sync,"access_token",side_effect=AssertionError("Unexpected OAuth")):
            report = intake.capture_new(store,BATCH_ID,SHA,"intake-v1")
        self.assertTrue(report["existing_batch"])
        self.assertNotIn("PRIVATE",json.dumps(report))
        self.assertNotIn("https://",json.dumps(report))

    def test_read_mode_missing_batch_does_not_refetch(self):
        db = FakeDB(); store = PostgresIntakeStore(db.connect)
        with patch.object(intake,"verify_revision"), patch.object(intake,"open_store",return_value=store), \
             patch.object(sync,"access_token",side_effect=AssertionError("Unexpected OAuth")):
            with self.assertRaisesRegex(contract.IntakeError,"NOT_FOUND_DO_NOT_REFETCH"):
                intake.main(["read","--batch-id",BATCH_ID,"--expected-sha",SHA,"--allow-live"])

    def test_new_capture_reuses_normalizer_and_body_first_without_existing_key_filter(self):
        from collectors.inoreader import probe
        raw = lambda n,body: ({"id":str(n),"title":"Title","summary":{"content":body}},"BOHUN_Test")
        entries = {str(i):raw(i,"<p>Body</p>" if i % 2 else "") for i in range(8)}
        db = FakeDB(); store = PostgresIntakeStore(db.connect)
        with patch.object(sync,"access_token",return_value="mock-token"), \
             patch.object(probe,"collect",return_value=(entries,{})) as collect, \
             patch.object(sync,"normalize",wraps=sync.normalize) as normalize:
            report = intake.capture_new(store,BATCH_ID,SHA,"intake-v1")
        self.assertEqual(report["items"],5)
        self.assertEqual(report["body_items"],4)
        self.assertEqual(collect.call_count,1)
        self.assertEqual(normalize.call_count,5)
        self.assertEqual(contract.validate_batch(store.read(BATCH_ID)[0])["items"][0]["external_id"],"1")

    def test_old_full_db_password_is_not_used(self):
        with patch.dict(os.environ,{"SUPABASE_DB_PASSWORD":"PRIVATE_PASSWORD"},clear=True):
            with self.assertRaisesRegex(contract.IntakeError,"LIMITED_DSN_REQUIRED"):
                intake.open_store()

    def test_invalid_invocation_does_not_read_db_or_fetch(self):
        store = PostgresIntakeStore(lambda: self.fail("Unexpected DB connection"))
        with self.assertRaises(contract.IntakeError):
            intake.capture_new(store,BATCH_ID,SHA,"INVALID VERSION")


class MigrationBoundaryTests(OfflineTest):
    def test_candidate_does_not_write_old_tables_or_expose_rpc(self):
        # A release boundary check, NOT proof that SQL parses/executes correctly.
        sql = Path("db/migrations/20261018_exp009_intake.sql").read_text()
        import re
        self.assertIsNone(re.search(r"(?:INSERT INTO|UPDATE|DELETE FROM|ALTER TABLE)\s+intelligence\.",sql,re.I))
        self.assertNotIn("CREATE FUNCTION public.",sql)
        self.assertNotIn("BYPASSRLS;",sql.replace("NOBYPASSRLS;",""))
        self.assertEqual(sql.count("ENABLE ROW LEVEL SECURITY"),4)
        self.assertIn("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA exp009",sql)
        self.assertNotIn("SECURITY DEFINER SET search_path = public",sql)


if __name__ == "__main__":
    unittest.main()
