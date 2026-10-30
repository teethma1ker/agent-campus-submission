"""工具层测试：读取/规则/时段/保存（幂等、故障注入、渠道隔离）。"""
import json
import unittest
from datetime import date

from helpers import payload_and_ctx  # noqa: F401  (确保 sys.path 设置)
from tools import load_context, build_registry, schema_error

ROOT_CTX = None


def fresh():
    ctx = load_context()
    reg = build_registry(ctx, date(2026, 10, 31))
    return ctx, reg


class ReadMaterialTest(unittest.TestCase):
    def test_missing_fields_reported(self):
        _, reg = fresh()
        obs = json.loads(reg["read_material"]("M04"))
        self.assertEqual(obs["missing_fields"], ["publish_time"])

    def test_unknown_material(self):
        _, reg = fresh()
        obs = json.loads(reg["read_material"]("M99"))
        self.assertIn("error", obs)


class CheckRulesTest(unittest.TestCase):
    def test_extreme_word_blocker(self):
        _, reg = fresh()
        obs = json.loads(reg["check_rules"]("M02"))
        self.assertEqual(obs["status"], "blocker")
        self.assertTrue(any("第一" in r for r in obs["reasons"]))

    def test_competitor_blocker(self):
        _, reg = fresh()
        obs = json.loads(reg["check_rules"]("M06"))
        self.assertEqual(obs["status"], "blocker")

    def test_partner_warning(self):
        _, reg = fresh()
        obs = json.loads(reg["check_rules"]("M05"))
        self.assertEqual(obs["status"], "warning")
        self.assertIn("星辰科技", "".join(obs["reasons"]))

    def test_clean_material_ok(self):
        _, reg = fresh()
        self.assertEqual(json.loads(reg["check_rules"]("M01"))["status"], "ok")


class QuerySlotsTest(unittest.TestCase):
    def test_occupied_slot_suggests_nearest(self):
        _, reg = fresh()
        obs = json.loads(reg["query_slots"]("M01"))
        self.assertEqual(obs["requested_slot"]["status"], "occupied")
        self.assertEqual(obs["suggested_slots"][0], "19:00")

    def test_missing_time(self):
        _, reg = fresh()
        obs = json.loads(reg["query_slots"]("M04"))
        self.assertEqual(obs["requested_slot"]["status"], "missing_time")
        self.assertTrue(obs["suggested_slots"])

    def test_non_integer_time_rejected(self):
        _, reg = fresh()
        obs = json.loads(reg["query_slots"]("M03"))
        self.assertEqual(obs["requested_slot"]["status"], "format_mismatch")

    def test_channel_isolation(self):
        # M16（小红书 18:00）与 M09（视频号 18:00）不同渠道，不应互相阻塞
        _, reg = fresh()
        obs = json.loads(reg["query_slots"]("M16"))
        self.assertEqual(obs["requested_slot"]["status"], "available")

    def test_video_outside_allowed_slots(self):
        # M12 视频 10:00？在允许列表内；用直播 M04 验证类型规则：直播允许 19-21
        ctx, reg = fresh()
        ctx["materials"]["M99x"] = dict(ctx["materials"]["M04"], id="M99x", publish_time="10:00")
        obs = json.loads(reg["query_slots"]("M99x"))
        self.assertEqual(obs["requested_slot"]["status"], "format_mismatch")


class SaveDraftTest(unittest.TestCase):
    def test_save_then_duplicate(self):
        ctx, reg = fresh()
        first = json.loads(reg["save_draft"]("M08", "10:00"))
        second = json.loads(reg["save_draft"]("M08", "11:00"))
        self.assertTrue(first["ok"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(len(ctx["draft_store"]), 1)
        self.assertEqual(ctx["draft_store"]["M08"]["slot"], "10:00")  # 原有草稿不被覆盖

    def test_fault_injection_fails_then_recovers(self):
        ctx, reg = fresh()
        ctx["faults"]["save_draft"] = {"error": "disk full", "remaining": 1}
        fail = json.loads(reg["save_draft"]("M08", "10:00"))
        ok = json.loads(reg["save_draft"]("M08", "10:00"))
        self.assertFalse(fail["ok"])
        self.assertIn("error", fail)
        self.assertTrue(ok["ok"])

    def test_invalid_slot(self):
        _, reg = fresh()
        obs = json.loads(reg["save_draft"]("M08", "25:99"))
        self.assertFalse(obs["ok"])


class SchemaTest(unittest.TestCase):
    def test_unknown_action_rejected(self):
        self.assertIsNotNone(schema_error("publish_now", {"material_id": "M01"}))

    def test_missing_args_rejected(self):
        self.assertIsNotNone(schema_error("save_draft", {"material_id": "M01"}))

    def test_valid_args_accepted(self):
        self.assertIsNone(schema_error("save_draft", {"material_id": "M01", "slot": "18:00",
                                                     "status": "pending_confirmation"}))


if __name__ == "__main__":
    unittest.main()
