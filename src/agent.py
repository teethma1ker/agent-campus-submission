"""Agent 主循环：模型选动作 → 执行工具 → 观察结果 → 模型决定下一步或结束。

工程要点：
- 步数上限 / 工具调用上限；超限返回明确的未完成状态（incomplete_limit_reached）。
- 动作与参数先做协议校验；非法输出不执行，作为观察反馈给模型重试。
- 最终报告由「工具真实返回值 + 草稿库真实状态」构建，不信任模型自述；
  并对模型文本做声明核对（claim check）：越权发布、虚报保存会被标注。
- 本 Agent 没有发布工具：published 恒为 false 是系统事实。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from gateway import (DEFAULT_DATE, SYSTEM_PROMPT, LLMClient, MockLLM,
                     extract_json, render_user_prompt)
from tools import build_registry, schema_error

TOOL_ACTIONS = ("read_material", "check_rules", "query_slots", "save_draft")
PUBLISH_CLAIMS = ("已发布", "发布成功", "已经上线", "已上线", "已推送")
SAVE_CLAIMS = ("已保存", "保存成功", "已完成保存", "已存草稿", "已入草稿", "已发布成功")


@dataclass
class Limits:
    max_steps: int = 20            # 单次请求的模型-动作循环上限
    max_tool_calls: int = 16       # 单次请求的工具调用上限
    max_protocol_errors: int = 5   # 连续非法模型输出上限


@dataclass
class RunResult:
    status: str                    # completed | incomplete_limit_reached | model_protocol_error | model_error
    request_id: str
    trace: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    drafts: list[dict] = field(default_factory=list)


def summarize_observation(action: str, obs: dict) -> str:
    if not isinstance(obs, dict):
        return str(obs)[:120]
    if obs.get("error"):
        return f"error: {obs['error']}"
    if action == "read_material":
        return (f"channel={obs.get('channel')} format={obs.get('format')} "
                f"publish_time={obs.get('publish_time')} missing={obs.get('missing_fields')}")
    if action == "check_rules":
        reasons = "；".join(obs.get("reasons", [])) or "无风险"
        return f"status={obs.get('status')}（{reasons}）"
    if action == "query_slots":
        req = obs.get("requested_slot", {})
        extra = f"（{req.get('reason')}）" if req.get("reason") else ""
        return (f"requested {req.get('slot')}→{req.get('status')}{extra}；"
                f"suggest={obs.get('suggested_slots')}")
    if action == "save_draft":
        if obs.get("duplicate"):
            return f"duplicate=true draft_id={obs.get('draft_id')}（已有草稿，未重复创建）"
        return f"saved draft_id={obs.get('draft_id')} slot={obs.get('slot')} status={obs.get('status')}"
    return str(obs)[:120]


class Agent:
    def __init__(self, gateway, ctx: dict, limits: Limits | None = None, today: date | None = None):
        self.gateway = gateway
        self.ctx = ctx
        self.limits = limits or Limits()
        self.today = today or date.today()

    def run_request(self, request: dict) -> RunResult:
        d = date.fromisoformat(request.get("date", DEFAULT_DATE))
        registry = build_registry(self.ctx, d)
        trace: list[dict] = []
        tool_calls = 0
        protocol_errors = 0
        status = "incomplete_limit_reached"
        finish_summary = None
        saved_this_run: list[str] = []
        duplicates: list[dict] = []
        failed_saves: list[dict] = []
        is_mock = isinstance(self.gateway, MockLLM)

        while True:
            if len(trace) >= self.limits.max_steps:
                status = "incomplete_limit_reached"
                break

            # ---- 取得下一步决策：真实模型 vs Mock 决策器
            if is_mock:
                decision = self.gateway.decide(request, trace)
            else:
                user_prompt = render_user_prompt(request, trace, self.today)
                try:
                    raw = self.gateway.complete(SYSTEM_PROMPT, user_prompt)
                except Exception as e:  # noqa: BLE001 - 模型不可用：明确失败并停止
                    trace.append({"step": len(trace) + 1, "thought": "", "action": "model_error",
                                  "arguments": {}, "observation_summary": f"模型调用失败：{e}",
                                  "decision_basis": "模型不可用，终止本次运行"})
                    status = "model_error"
                    break
                try:
                    decision = extract_json(raw)
                except Exception as e:  # noqa: BLE001
                    protocol_errors += 1
                    trace.append({"step": len(trace) + 1, "thought": "模型输出不可解析",
                                  "action": "protocol_error", "arguments": {},
                                  "observation_summary": f"输出被拒绝：{e}；请按 JSON 协议重试",
                                  "decision_basis": "解析失败，未执行任何动作"})
                    if protocol_errors >= self.limits.max_protocol_errors:
                        status = "model_protocol_error"
                        break
                    continue

            action = decision.get("action")
            args = decision.get("arguments") or {}
            thought = str(decision.get("thought", ""))[:300]

            if action == "finish":
                finish_summary = str((args or {}).get("summary", ""))[:500]
                trace.append({"step": len(trace) + 1, "thought": thought, "action": "finish",
                              "arguments": args, "observation_summary": "会话结束",
                              "decision_basis": thought})
                status = "completed"
                break

            err = schema_error(action, args)
            if err:
                protocol_errors += 1
                trace.append({"step": len(trace) + 1, "thought": thought,
                              "action": "invalid_action", "arguments": args if isinstance(args, dict) else {},
                              "observation_summary": f"动作被拒绝：{err}；请修正后重试",
                              "decision_basis": "动作/参数不符合工具协议，未执行"})
                if protocol_errors >= self.limits.max_protocol_errors:
                    status = "model_protocol_error"
                    break
                continue

            if tool_calls >= self.limits.max_tool_calls:
                status = "incomplete_limit_reached"
                trace.append({"step": len(trace) + 1, "thought": thought, "action": "limit_reached",
                              "arguments": {}, "observation_summary": f"工具调用次数已达上限（{self.limits.max_tool_calls}），停止执行",
                              "decision_basis": "达到工具调用上限"})
                break

            try:
                obs_text = registry[action](**args)
                obs = json.loads(obs_text)
            except Exception as e:  # noqa: BLE001 - 工具异常转化为观察
                obs = {"error": f"工具执行异常：{e}"}
            tool_calls += 1
            if action == "save_draft":
                if obs.get("ok") and not obs.get("duplicate"):
                    saved_this_run.append(obs.get("draft_id"))
                elif obs.get("duplicate"):
                    duplicates.append({"material_id": args.get("material_id"), "draft_id": obs.get("draft_id")})
                elif not obs.get("ok"):
                    failed_saves.append({"material_id": args.get("material_id"), "error": obs.get("error")})

            trace.append({
                "step": len(trace) + 1,
                "thought": thought,
                "action": action,
                "arguments": args,
                "observation_summary": summarize_observation(action, obs),
                "observation": obs,
                "decision_basis": thought,
            })

        report = self._build_report(request, trace, status, finish_summary,
                                    saved_this_run, duplicates, failed_saves, tool_calls)
        material_ids = {m["id"] for m in request["materials"]}
        drafts = [d for d in self.ctx["draft_store"].values() if d["material_id"] in material_ids]
        return RunResult(status=status, request_id=request.get("id", "request"), trace=trace,
                         report=report, drafts=drafts)

    # -------------------------------------------------------------- 报告：以工具真值为准
    def _build_report(self, request, trace, status, finish_summary,
                      saved_this_run, duplicates, failed_saves, tool_calls) -> dict:
        store = self.ctx["draft_store"]
        entries: list[dict] = []
        for m in request["materials"]:
            mid = m["id"]
            m_trace = [t for t in trace if t.get("arguments", {}).get("material_id") == mid
                       and t["action"] in TOOL_ACTIONS]
            read_obs = next((t["observation"] for t in m_trace if t["action"] == "read_material"), None) or {}
            check_obs = next((t["observation"] for t in m_trace if t["action"] == "check_rules"), None) or {}
            query_obs = next((t["observation"] for t in m_trace if t["action"] == "query_slots"), None) or {}

            if read_obs.get("error"):
                entries.append({"material_id": mid, "title": m["fields"].get("title"),
                                "audit_status": "error", "risk_reasons": [read_obs["error"]],
                                "missing_fields": [], "suggested_slots": [],
                                "draft": None, "draft_save_status": "not_saved",
                                "pending_confirmations": [f"素材读取失败：{read_obs['error']}"],
                                "published": False})
                continue

            draft = store.get(mid)
            missing = read_obs.get("missing_fields", []) if read_obs else []
            risk = check_obs.get("reasons", []) if check_obs else []
            audit = check_obs.get("status") or ("not_checked" if read_obs else "not_processed")
            req = query_obs.get("requested_slot") or {}
            req_status = req.get("status")
            suggested = query_obs.get("suggested_slots", [])
            pending: list[str] = []

            if not read_obs:
                pending.append("本次运行未处理到该素材（执行中断或触达上限）")
            if missing:
                pending.append(f"素材缺少 {'/'.join(missing)}，无法确定发布时间，需补齐资料；"
                               f"建议时段 {'/'.join(suggested) or '待定'}（仅建议，未保存）")
            if check_obs.get("status") == "blocker":
                pending.append("存在阻断级风险，未进入排期，需整改后重新提交")
            if check_obs.get("status") == "warning":
                pending.append("存在规则提醒：" + "；".join(risk))
            if req_status in ("occupied", "blocked", "format_mismatch", "out_of_hours"):
                pending.append(f"硬性时段 {req.get('slot')} 不可用（{req.get('reason')}）；"
                               f"建议替代 {'/'.join(suggested) or '暂无'}，等待用户确认，未擅自改期")

            dup = next((x for x in duplicates if x["material_id"] == mid), None)
            failed = next((f for f in failed_saves if f["material_id"] == mid), None)
            draft_view = None
            draft_status = "not_saved"
            if draft:
                draft_view = {"draft_id": draft["draft_id"], "slot": draft["slot"],
                              "status": draft["status"],
                              "saved_this_run": draft["draft_id"] in saved_this_run}
                draft_status = "saved" if draft["draft_id"] in saved_this_run else "existing"
            if dup:
                draft_status = "duplicate_existing"
                pending.append(f"检测到已有草稿 {dup['draft_id']}，本次未重复保存")
            if failed and not draft:
                draft_status = "failed"
                pending.append(f"草稿保存失败：{failed['error']}；本次未保存成功")
            if read_obs and not missing and not draft and check_obs.get("status") != "blocker" \
                    and req_status == "available" and not failed:
                pending.append("处理未完成：可用草稿尚未保存")

            entries.append({
                "material_id": mid,
                "title": read_obs.get("title") if read_obs else m["fields"].get("title"),
                "channel": read_obs.get("channel") if read_obs else m["fields"].get("channel"),
                "requested_publish_time": read_obs.get("publish_time") if read_obs else m["fields"].get("publish_time"),
                "hard_requirement": read_obs.get("required") if read_obs else None,
                "audit_status": audit,
                "risk_reasons": risk,
                "missing_fields": missing,
                "suggested_slots": suggested,
                "requested_slot_status": req_status,
                "draft": draft_view,
                "draft_save_status": draft_status,
                "pending_confirmations": pending,
                "published": False,
            })

        # ---- 声明核对：模型文字 vs 系统事实
        claim_notes = []
        text = " ".join([finish_summary or ""] + [t.get("thought", "") for t in trace])
        # 去掉否定表述，避免“未保存成功/保存失败/未发布”被误判为肯定声明
        negations = ("未保存成功", "没有保存成功", "保存失败", "未发布", "尚未发布", "未上线", "不能发布", "禁止发布")
        positive = text
        for n in negations:
            positive = positive.replace(n, "")
        if any(w in positive for w in PUBLISH_CLAIMS):
            claim_notes.append("模型文本出现“已发布/已上线”类表述，但系统不存在发布能力，判定为越权表述")
        summary_positive = finish_summary or ""
        for n in negations:
            summary_positive = summary_positive.replace(n, "")
        # 保存声明只核对模型的最终总结（中间步骤的“重试中/失败”叙述不算声明）
        if any(w in summary_positive for w in SAVE_CLAIMS) and not saved_this_run:
            claim_notes.append("模型总结声称保存成功，但本次草稿库无新增，判定为虚报")
        failed_tool_saves = [t for t in trace if t["action"] == "save_draft"
                             and not (t.get("observation") or {}).get("ok")]
        if failed_tool_saves and any(w in summary_positive for w in SAVE_CLAIMS) and not saved_this_run:
            claim_notes.append("工具已返回保存失败，模型总结仍声称保存成功，判定为虚报")

        return {
            "request_id": request.get("id"),
            "date": request.get("date", DEFAULT_DATE),
            "status": status,
            "status_note": {
                "completed": "本批素材已处理完毕",
                "incomplete_limit_reached": "达到执行步数/工具调用上限，存在未完成项",
                "model_protocol_error": "模型连续输出非法协议内容，运行中止",
                "model_error": "模型调用失败，运行中止",
            }.get(status, status),
            "model_summary": finish_summary,
            "claim_check": {"passed": not claim_notes, "issues": claim_notes},
            "materials": entries,
            "saved_this_run": saved_this_run,
            "duplicates": duplicates,
            "failed_saves": failed_saves,
            "publishing": "disabled：系统只保存草稿，不存在发布能力",
            "tool_calls": tool_calls,
            "steps": len(trace),
        }
