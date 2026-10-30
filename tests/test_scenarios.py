"""场景级测试：对应题目的六类验收 + 执行上限 + 确认边界 + 防虚报。"""
import json
import sys
import unittest
from datetime import date

from helpers import ScriptLLM, payload_and_ctx
from agent import Agent, Limits
from gateway import MockLLM


def run(request_id, limits=None, gateway=None):
    payload, ctx = payload_and_ctx(request_id)
    agent = Agent(gateway or MockLLM(), ctx, limits=limits or Limits(), today=date(2026, 10, 30))
    return agent.run_request(payload), ctx


def entry(report, mid):
    return next(e for e in report["materials"] if e["material_id"] == mid)


class NormalScheduleTest(unittest.TestCase):
    def test_normal_schedule_saves_confirmed_draft(self):
        result, ctx = run("R1-01")
        self.assertEqual(result.status, "completed")
        for mid, slot in (("M08", "10:00"), ("M07", "19:00")):
            e = entry(result.report, mid)
            self.assertEqual(e["audit_status"], "ok")
            self.assertEqual(e["draft"]["slot"], slot)
            self.assertEqual(e["draft"]["status"], "confirmed")
            self.assertFalse(e["published"])
        self.assertEqual(len(ctx["draft_store"]), 2)

    def test_published_always_false(self):
        result, _ = run("R1-01")
        self.assertTrue(all(not e["published"] for e in result.report["materials"]))
        self.assertIn("不存在发布能力", result.report["publishing"])


class MissingInfoTest(unittest.TestCase):
    def test_missing_time_no_draft_and_pending(self):
        result, ctx = run("R2-01")
        e = entry(result.report, "M04")
        self.assertIn("publish_time", e["missing_fields"])
        self.assertEqual(e["draft_save_status"], "not_saved")
        self.assertIsNone(e["draft"])
        self.assertIn("19:00", e["suggested_slots"])
        self.assertTrue(any("补充" in p or "补齐" in p for p in e["pending_confirmations"]))
        self.assertEqual(ctx["draft_store"], {})


class RuleRiskTest(unittest.TestCase):
    def test_blocker_no_draft(self):
        for req_id, mid in (("R3-01", "M02"), ("R3-02", "M06")):
            result, ctx = run(req_id)
            e = entry(result.report, mid)
            self.assertEqual(e["audit_status"], "blocker")
            self.assertIsNone(e["draft"])
            self.assertNotIn(mid, ctx["draft_store"])

    def test_warning_draft_pending_confirmation(self):
        result, ctx = run("R4-01")
        e = entry(result.report, "M05")
        self.assertEqual(e["audit_status"], "warning")
        self.assertEqual(e["draft"]["status"], "pending_confirmation")
        self.assertTrue(any("法务" in p for p in e["pending_confirmations"]))


class SlotConflictTest(unittest.TestCase):
    def test_conflict_keeps_hard_time_and_marks_pending(self):
        result, ctx = run("R4-02")
        e = entry(result.report, "M01")
        self.assertEqual(e["requested_slot_status"], "occupied")
        self.assertEqual(e["draft"]["slot"], "18:00")            # 未擅自改期
        self.assertEqual(e["draft"]["status"], "pending_confirmation")
        self.assertEqual(e["suggested_slots"][0], "19:00")
        self.assertIn("18:00", [d["slot"] for d in ctx["draft_store"].values()])

    def test_off_grid_time_conflict(self):
        result, _ = run("R4-03")
        e = entry(result.report, "M03")
        self.assertEqual(e["requested_slot_status"], "format_mismatch")
        self.assertEqual(e["draft"]["slot"], "19:30")
        self.assertEqual(e["draft"]["status"], "pending_confirmation")


class DuplicateSubmitTest(unittest.TestCase):
    def test_resubmit_existing_draft_not_duplicated(self):
        result, ctx = run("R5-02")
        e = entry(result.report, "M01")
        self.assertEqual(e["draft_save_status"], "duplicate_existing")
        self.assertEqual(len(ctx["draft_store"]), 1)   # 只有一份草稿
        self.assertEqual(result.report["duplicates"][0]["draft_id"], "D0001")

    def test_two_submissions_same_store_one_draft(self):
        """模拟同一素材先后两次提交：同一草稿库只产生一份草稿。"""
        payload, ctx = payload_and_ctx("R5-01")
        agent = Agent(MockLLM(), ctx, today=date(2026, 10, 30))
        result1 = agent.run_request(payload)
        result2 = Agent(MockLLM(), ctx, today=date(2026, 10, 30)).run_request(payload)
        self.assertEqual(result1.report["materials"][0]["draft_save_status"], "saved")
        self.assertEqual(result2.report["materials"][0]["draft_save_status"], "duplicate_existing")
        self.assertEqual(len(ctx["draft_store"]), 1)


class SaveFailureTest(unittest.TestCase):
    def test_save_failure_reported_not_faked(self):
        result, ctx = run("R6-01")
        e = entry(result.report, "M08")
        self.assertEqual(e["draft_save_status"], "failed")
        self.assertTrue(any("保存失败" in p for p in e["pending_confirmations"]))
        self.assertEqual(ctx["draft_store"], {})
        saves = [t for t in result.trace if t["action"] == "save_draft"]
        self.assertEqual(len(saves), 2)                       # 初次 + 重试
        self.assertTrue(all(not t["observation"].get("ok") for t in saves))


class LimitTest(unittest.TestCase):
    def test_step_limit_returns_incomplete(self):
        result, ctx = run("R7-01", limits=Limits(max_steps=3))
        self.assertEqual(result.status, "incomplete_limit_reached")
        self.assertIn("上限", result.report["status_note"])
        self.assertLessEqual(len(result.trace), 3)

    def test_tool_call_limit_returns_incomplete(self):
        payload, ctx = payload_and_ctx("R1-01")
        agent = Agent(MockLLM(), ctx, limits=Limits(max_steps=50, max_tool_calls=5),
                      today=date(2026, 10, 30))
        result = agent.run_request(payload)
        self.assertEqual(result.status, "incomplete_limit_reached")
        self.assertLessEqual(result.report["tool_calls"], 5)
        unprocessed = [e for e in result.report["materials"] if e["audit_status"] in ("not_processed", "not_checked")
                       or e["draft_save_status"] == "not_saved"]
        self.assertTrue(unprocessed, "超限时必须留下明确的未完成项")


class GuardrailTest(unittest.TestCase):
    def test_publish_claim_flagged(self):
        script = ScriptLLM([
            {"thought": "直接发布", "action": "publish_now", "arguments": {"material_id": "M01"}},
            {"thought": "保存后即可发布", "action": "save_draft",
             "arguments": {"material_id": "M01", "slot": "18:00"}},
            {"thought": "已发布成功", "action": "finish", "arguments": {"summary": "M01 已发布成功"}},
        ])
        payload, ctx = payload_and_ctx("R4-02")
        result = Agent(script, ctx, today=date(2026, 10, 30)).run_request(payload)
        actions = [t["action"] for t in result.trace]
        self.assertIn("invalid_action", actions)               # publish_now 被拒绝
        self.assertFalse(result.report["claim_check"]["passed"])
        self.assertTrue(any("越权" in i or "虚报" in i for i in result.report["claim_check"]["issues"]))
        self.assertFalse(result.report["materials"][0]["published"])

    def test_fake_save_success_flagged(self):
        script = ScriptLLM([
            {"thought": "读取", "action": "read_material", "arguments": {"material_id": "M08"}},
            {"thought": "查规则", "action": "check_rules", "arguments": {"material_id": "M08"}},
            {"thought": "查时段", "action": "query_slots", "arguments": {"material_id": "M08"}},
            {"thought": "保存", "action": "save_draft", "arguments": {"material_id": "M08", "slot": "10:00"}},
            {"thought": "已保存成功", "action": "finish", "arguments": {"summary": "M08 草稿保存成功"}},
        ])
        payload, ctx = payload_and_ctx("R6-01")   # 故障注入：保存必失败
        result = Agent(script, ctx, today=date(2026, 10, 30)).run_request(payload)
        self.assertEqual(entry(result.report, "M08")["draft_save_status"], "failed")
        self.assertFalse(result.report["claim_check"]["passed"])
        self.assertTrue(result.report["claim_check"]["issues"])


class ProtocolTest(unittest.TestCase):
    def test_garbage_output_then_finish(self):
        script = ScriptLLM(["这不是 JSON", "也不是", {"thought": "收尾", "action": "finish",
                                                     "arguments": {"summary": "ok"}}])
        payload, ctx = payload_and_ctx("R1-02")
        result = Agent(script, ctx, today=date(2026, 10, 30)).run_request(payload)
        self.assertEqual(result.status, "completed")
        self.assertEqual([t["action"] for t in result.trace].count("protocol_error"), 2)


if __name__ == "__main__":
    unittest.main()
