"""Offline origin/identity/contract scenarios; no claims of live delivery."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

with (patch("socket.socket", side_effect=AssertionError("IMPORT_NETWORK")),
      patch("socket.getaddrinfo", side_effect=AssertionError("IMPORT_DNS")),
      patch("urllib.request.urlopen", side_effect=AssertionError("IMPORT_HTTP")),
      patch("sqlite3.connect", side_effect=AssertionError("IMPORT_DB")),
      patch("subprocess.Popen", side_effect=AssertionError("IMPORT_PROCESS"))):
    from collectors.inoreader import batch_delivery as delivery
    from collectors.inoreader import intake_contract as contract


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SHA = "a" * 40


def make_plan(count=12):
    return dict(schema="bohun-inoreader-delivery-plan-v1", status="REVIEW_INPUT_NOT_LIVE_APPROVAL",
                purpose="Synthetic offline test only", accepted_design_sha256="a"*64, master_sha256="b"*64,
                profile_limit=count, items_per_origin=1, caps=dict(delivery.CAPS),
                live_authorized=False, intake_apply_authorized=False,
                profiles=[dict(batch_profile_id=f"A{i:02}", catalog_id=f"synthetic_{i}", name=f"Publisher {i}",
                               url=f"https://p{i}.example.test/feed/", folder=("BOHUN_MEDIA", "BOHUN_GOV",
                               "BOHUN_RESEARCH", "BOHUN_OPPORTUNITIES")[i % 4], master_slot="S01",
                               batch_id=f"00000000-0000-4000-8000-{i:012}") for i in range(count)])


def snapshot(plan):
    # Deliberately reverse both feed and folder order, beyond the legacy first-three window.
    return {"subscriptions": [dict(id="feed/"+p["url"], title="Same title", categories=[
        dict(id="user/-/label/"+p["folder"], label=p["folder"])]) for p in reversed(plan["profiles"])], "complete": True}


def article(feed_id, *, key=None, body=True):
    value = dict(id=key or "item/"+feed_id, origin=dict(streamId=feed_id, title="Synthetic Publisher"),
                 title="Synthetic article", published=1792886400,
                 canonical=[dict(href="https://example.test/article")])
    if body:
        value["summary"] = {"content": "<p>Синтетичний текст</p>"}
    return value


def run(plan, feed=None, snap=None):
    return delivery.collect(plan, snapshot(plan) if snap is None else snap,
                            feed or (lambda fid: {"items": [article(fid)]}),
                            captured_at=NOW, producer_sha=SHA, pipeline_version="preview-v1")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.getaddrinfo", "urllib.request.urlopen", "sqlite3.connect", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("UNEXPECTED_EXTERNAL_IO"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_twelve_origins_four_folders_all_have_own_valid_batch(self):
        plan = make_plan()
        original = copy.deepcopy(plan)
        calls = []
        result = run(plan, lambda fid: calls.append(fid) or {"items": [article(fid)]})
        self.assertEqual(len(calls), 12)
        self.assertEqual(len(set(calls)), 12)
        self.assertEqual(result["status"], "PREVIEW_COMPLETE")
        self.assertEqual(len(result["batches"]), 12)
        self.assertEqual(plan, original)
        for p in plan["profiles"]:
            saved = result["batches"][p["batch_profile_id"]]
            batch = contract.PreparedBatch(saved["manifest_json"], saved["manifest_sha256"], tuple(saved["payloads"]))
            manifest = contract.validate_batch(batch)
            self.assertEqual(manifest["item_count"], 1)
            item = json.loads(batch.payloads[0])["item"]
            self.assertEqual(item["raw_data"]["origin_stream_id"], "feed/"+p["url"])
            self.assertEqual(item["raw_data"]["folder"], p["folder"])
        self.assertFalse(result["public_release"])

    def test_missing_and_wrong_folder_do_not_fetch_or_get_replaced(self):
        p = make_plan(3)
        s = snapshot(p)
        s["subscriptions"] = s["subscriptions"][:2]
        s["subscriptions"][0]["categories"] = []
        calls = []
        r = run(p, lambda fid: calls.append(fid) or {"items": [article(fid)]}, s)
        self.assertEqual([x["status"] for x in r["outcomes"]],
                         ["MISSING_SUBSCRIPTION", "PREPARED", "REVIEW_FOLDER"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(r["status"], "PREVIEW_PARTIAL")

    def test_languages_are_not_merged_by_host_or_title(self):
        p = make_plan(2)
        for i, language in enumerate(("en", "uk")):
            p["profiles"][i]["url"] = "https://publisher.example.test/"+language+"/feed/"
        self.assertEqual(len(run(p)["batches"]), 2)
        p["profiles"][1]["url"] = p["profiles"][0]["url"].upper().replace("/EN/FEED/", "/en/feed/")
        with self.assertRaises(delivery.BatchError):
            run(p)

    def test_incomplete_or_duplicate_snapshot_stops_before_stream_reads(self):
        p = make_plan(1)
        for change in ({"continuation": "more"}, {"complete": False}, {"nextPageToken": "next"}):
            s = snapshot(p) | change
            with self.assertRaisesRegex(delivery.BatchError, "SNAPSHOT_INCOMPLETE"):
                run(p, lambda _: self.fail("must not fetch"), s)
        s = snapshot(p)
        s["subscriptions"].append(copy.deepcopy(s["subscriptions"][0]))
        with self.assertRaisesRegex(delivery.BatchError, "SNAPSHOT_ID"):
            run(p, lambda _: self.fail("must not fetch"), s)

    def test_ambiguous_endpoint_is_review_not_first_match(self):
        p = make_plan(1)
        s = snapshot(p)
        s["subscriptions"].append(dict(id="feed/opaque-other-id", url=p["profiles"][0]["url"],
                                         categories=s["subscriptions"][0]["categories"]))
        r = run(p, lambda _: self.fail("must not fetch"), s)
        self.assertEqual(r["outcomes"][0]["status"], "REVIEW_AMBIGUOUS_ENDPOINT")

    def test_empty_metadata_and_bad_origin_remain_individual_partial_states(self):
        p = make_plan(3)
        def fetch(fid):
            if "p0." in fid:
                return {"items": []}
            v = article(fid, body=False)
            if "p2." in fid:
                v["origin"]["streamId"] = "feed/OTHER"
            return {"items": [v], "continuation": "not-followed"}
        r = run(p, fetch)
        self.assertEqual([x["status"] for x in r["outcomes"]],
                         ["WAITING_FOR_DELIVERY", "PREPARED_PARTIAL", "HOLD_ORIGIN_OR_ID"])
        self.assertEqual(len(r["batches"]), 1)

    def test_cross_origin_native_collision_holds_both(self):
        r = run(make_plan(2), lambda fid: {"items": [article(fid, key="same-native-id")]})
        self.assertEqual([x["status"] for x in r["outcomes"]], ["HOLD_NATIVE_ID_COLLISION"]*2)
        self.assertEqual(r["batches"], {})

    def test_network_uncertainty_stops_remaining_without_losing_first_batch(self):
        calls = []
        def fetch(fid):
            calls.append(fid)
            if len(calls) == 2:
                raise RuntimeError("CREDENTIAL_SENTINEL")
            return {"items": [article(fid)]}
        r = run(make_plan(4), fetch)
        self.assertEqual(len(calls), 2)
        self.assertEqual([x["status"] for x in r["outcomes"]],
                         ["PREPARED", "STOPPED", "NOT_ATTEMPTED_AFTER_STOP", "NOT_ATTEMPTED_AFTER_STOP"])
        self.assertNotIn("CREDENTIAL_SENTINEL", delivery.json_text(r))

    def test_extra_items_invalid_payload_and_bad_producer_do_not_write(self):
        p = make_plan(1)
        self.assertEqual(run(p, lambda fid: {"items": [article(fid)]*2})["outcomes"][0]["status"], "HOLD_ITEM_SHAPE")
        def bad(fid):
            return {"items": [article(fid) | {"published": True}]}
        self.assertEqual(run(p, bad)["outcomes"][0]["status"], "HOLD_PAYLOAD_CONTRACT")
        with self.assertRaisesRegex(delivery.BatchError, "PRODUCER_INVALID"):
            delivery.collect(p, snapshot(p), lambda _: self.fail("no fetch"), captured_at=NOW,
                             producer_sha="wrong", pipeline_version="preview")

    def test_plan_and_json_reject_ambiguous_untrusted_inputs(self):
        for mutate in (lambda p: p.update(profile_limit=True), lambda p: p["caps"].update(requests=101),
                       lambda p: p["profiles"][0].update(url="https://x.test/feed?token=PRIVATE"),
                       lambda p: p["profiles"][0].update(batch_id="../escape"),
                       lambda p: p.update(intake_apply_authorized=True)):
            p = make_plan(1)
            mutate(p)
            with self.assertRaises(delivery.BatchError):
                delivery.validate_plan(p)
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'"\\ud800"', b'"\\u0000"'):
            with self.assertRaises(delivery.BatchError):
                delivery.parse_json(raw)

    def test_saved_bundle_replay_is_byte_identical_and_never_fetches(self):
        bundle = run(make_plan(2))
        data = delivery.json_text(bundle)
        loaded = delivery.parse_json(data.encode())
        self.assertEqual(data, delivery.json_text(loaded))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            root = Path(temp)
            delivery.publish(loaded, root/"first", root=root)
            delivery.publish(loaded, root/"second", root=root)
            for name in ("preview.json", "run_manifest.json"):
                self.assertEqual((root/"first"/name).read_bytes(), (root/"second"/name).read_bytes())
            self.assertEqual(delivery.read_saved_preview(root/"first", root=root), loaded)
            manifest = json.loads((root/"first/run_manifest.json").read_text())
            self.assertEqual(manifest["files"]["preview.json"], delivery.digest((root/"first/preview.json").read_text()))
            with self.assertRaisesRegex(delivery.BatchError, "OUTPUT_EXISTS"):
                delivery.publish(loaded, root/"first", root=root)

    def test_saved_bundle_tampering_and_incomplete_manifest_are_rejected(self):
        bundle = run(make_plan(1))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            root = Path(temp)
            delivery.publish(bundle, root/"saved", root=root)
            (root/"saved/preview.json").write_text('{}')
            with self.assertRaisesRegex(delivery.BatchError, "SAVED_PREVIEW_INVALID"):
                delivery.read_saved_preview(root/"saved", root=root)
            (root/"partial").mkdir()
            (root/"partial/run_manifest.json").write_text('')
            with self.assertRaises(delivery.BatchError):
                delivery.read_saved_preview(root/"partial", root=root)
            bundle["batches"].clear()
            delivery.publish(bundle, root/"inconsistent", root=root)
            with self.assertRaisesRegex(delivery.BatchError, "SAVED_PREVIEW_INVALID"):
                delivery.read_saved_preview(root/"inconsistent", root=root)

    def test_untrusted_exception_message_is_never_a_reported_error_code(self):
        def broken(_):
            raise delivery.BatchError("SECRET_SENTINEL")
        result = run(make_plan(1), broken)
        self.assertEqual(result["outcomes"][0]["reason"], "INTERNAL_ERROR")
        self.assertNotIn("SECRET_SENTINEL", delivery.json_text(result))

    def test_output_rejects_traversal_symlinks_and_keeps_incomplete_reservation(self):
        bundle = run(make_plan(1))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            root = Path(temp)
            with self.assertRaises(delivery.BatchError):
                delivery.publish(bundle, root/".."/"escape", root=root)
            with self.assertRaises(delivery.BatchError):
                delivery.publish(bundle, root/"authorizations", root=root)
            (root/"linked").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(delivery.BatchError, "OUTPUT_SYMLINK"):
                delivery.publish(bundle, root/"linked/child", root=root/"linked")
            original = delivery.json_text
            calls = []
            def fail_manifest(value):
                calls.append(1)
                if len(calls) == 2:
                    raise ValueError("simulated local failure")
                return original(value)
            with patch.object(delivery, "json_text", side_effect=fail_manifest):
                with self.assertRaises(ValueError):
                    delivery.publish(bundle, root/"partial", root=root)
            self.assertTrue((root/"partial/preview.json").exists())
            self.assertFalse((root/"partial/run_manifest.json").exists())
            with self.assertRaises(delivery.BatchError):
                delivery.publish(bundle, root/"partial", root=root)
