"""命令行入口。

用法示例：
  python -m src.cli                        # 列出待处理素材与对应请求
  python -m src.cli --requests R4-02       # 运行指定请求（默认 mock 模式）
  python -m src.cli --requests R4-02 --model real   # 真实模型（需 .env 配置）
  python -m src.cli --all --trace-dir examples/traces
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent, Limits  # noqa: E402
from gateway import build_gateway, load_dotenv  # noqa: E402
from tools import load_context  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_requests() -> list[dict]:
    with open(os.path.join(ROOT, "data", "requests.json"), encoding="utf-8") as f:
        return json.load(f)


def build_request_ctx(req: dict):
    """构建单次请求独立的运行上下文（数据 + 预置草稿 + 故障注入）。"""
    ctx = load_context(os.path.join(ROOT, "data"))
    # 每个请求以请求日期为基准排期
    ctx["schedule"]["schedule_date"] = req.get("date", "2026-10-31")
    for mid, draft in (req.get("existing_draft") or {}).items():
        ctx["sequences"] += 1
        mat = ctx["materials"].get(mid, {})
        ctx["draft_store"][mid] = {
            "draft_id": f"D{ctx['sequences']:04d}",
            "material_id": mid,
            "title": mat.get("title"),
            "channel": mat.get("channel"),
            "format": mat.get("format"),
            "slot": draft["slot"],
            "status": draft.get("status", "pending_confirmation"),
            "created_at": "2026-10-31T09:00:00",
        }
    if req.get("faults", {}).get("save_draft"):
        ctx["faults"]["save_draft"] = dict(req["faults"]["save_draft"])
    return ctx


def materials_view(ctx: dict, ids: list[str]) -> list[dict]:
    out = []
    for mid in ids:
        m = ctx["materials"].get(mid, {})
        out.append({
            "id": mid,
            "fields": {k: m.get(k) for k in ("title", "format", "channel", "theme",
                                            "publish_time", "publish_due", "required")},
            "description": m.get("description", ""),
        })
    return out


def make_request_payload(req: dict) -> tuple[dict, dict]:
    ctx = build_request_ctx(req)
    payload = {
        "id": req["id"],
        "date": req.get("date", "2026-10-31"),
        "channel": "（见各素材）",
        "theme": req.get("scenario", ""),
        "request": req.get("request", ""),
        "materials": materials_view(ctx, req["materials"]),
        "existing_drafts": list(ctx["draft_store"].values()),
        "schedule": ctx["schedule"],
        "rules": ctx["rules"],
    }
    return payload, ctx


def pending_overview() -> None:
    ctx = load_context(os.path.join(ROOT, "data"))
    occupied = {o["material_id"] for o in ctx["schedule"].get("occupied", [])}
    reqs = load_requests()
    wants = {mid: r["id"] for r in reqs for mid in r["materials"]}
    print(f"排期日期 {ctx['schedule']['schedule_date']}｜营业时间 "
          f"{ctx['rules']['business_hours']['start']}–{ctx['rules']['business_hours']['end']}")
    print(f"\n{'素材':<5}{'标题':<14}{'渠道':<7}{'格式':<5}{'要求时间':<9}{'截止':<12}{'状态'}")
    for mid, m in ctx["materials"].items():
        if mid in occupied:
            status = "已排期"
        elif mid in wants:
            status = f"待处理（请求 {wants[mid]}）"
        else:
            status = "待处理（暂无请求）"
        print(f"{mid:<5}{m.get('title',''):<14}{m.get('channel',''):<7}{m.get('format',''):<5}"
              f"{str(m.get('publish_time') or '待定'):<9}{m.get('publish_due',''):<12}{status}")
    print("\n运行请求：python -m src.cli --requests " + ", ".join(r["id"] for r in reqs))


def print_result(req: dict, result, mode: str) -> None:
    print(f"\n=== {req['id']}｜{req['scenario']}｜排期 {req.get('date')}｜模型 {mode} ===")
    rep = result.report
    print(f"状态：{rep['status']}（{rep['status_note']}）｜步数 {rep['steps']}｜工具调用 {rep['tool_calls']}")
    for e in rep["materials"]:
        draft = e["draft"]
        draft_txt = f"{draft['draft_id']}@{draft['slot']} {draft['status']}" if draft else "无草稿"
        print(f"- {e['material_id']} {e.get('title', '')}｜审核 {e['audit_status']}｜"
              f"草稿 {draft_txt}（{e['draft_save_status']}）｜发布 否")
        if e["risk_reasons"]:
            print(f"    风险：{'；'.join(e['risk_reasons'])}")
        if e["missing_fields"]:
            print(f"    缺失：{', '.join(e['missing_fields'])}｜建议：{e['suggested_slots']}")
        for p in e["pending_confirmations"]:
            print(f"    待确认：{p}")
    if rep["claim_check"]["issues"]:
        print(f"!! 声明核对未通过：{rep['claim_check']['issues']}")
    print(f"草稿库（本次请求）：{[(d['draft_id'], d['material_id'], d['slot'], d['status']) for d in result.drafts]}")


def main() -> int:
    load_dotenv(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser(description="素材审核与发布排期 Agent")
    ap.add_argument("--requests", help="请求 ID，逗号分隔（如 R1-01,R4-02）")
    ap.add_argument("--all", action="store_true", help="运行 data/requests.json 中全部请求")
    ap.add_argument("--model", choices=["mock", "real", "auto"], default=None,
                    help="mock=离线决策器；real=真实 LLM；auto=读环境变量 MODEL_MODE（默认 mock）")
    ap.add_argument("--limit-steps", type=int, default=20, help="单次请求的模型步数上限")
    ap.add_argument("--limit-tools", type=int, default=16, help="单次请求的工具调用上限")
    ap.add_argument("--trace-dir", help="把每个请求的 trace 与结果 JSON 写入该目录")
    ap.add_argument("--today", type=str, default=None, help="模拟当天日期 YYYY-MM-DD（默认系统日期）")
    args = ap.parse_args()

    reqs = load_requests()
    by_id = {r["id"]: r for r in reqs}
    today = date.fromisoformat(args.today) if args.today else date.today()

    if not args.requests and not args.all:
        pending_overview()
        return 0

    chosen = reqs if args.all else [by_id[i.strip()] for i in args.requests.split(",") if i.strip() in by_id]
    if not chosen:
        print("没有可运行的请求（检查 --requests 的 ID）")
        return 2

    mode = args.model or os.environ.get("MODEL_MODE", "mock")
    failures = 0
    for req in chosen:
        payload, ctx = make_request_payload(req)
        gateway = build_gateway("mock" if mode == "mock" else "real")
        limits = Limits(max_steps=req.get("limit_steps", args.limit_steps),
                        max_tool_calls=req.get("limit_tools", args.limit_tools))
        agent = Agent(gateway, ctx, limits=limits, today=today)
        result = agent.run_request(payload)
        print_result(req, result, mode)
        if args.trace_dir:
            os.makedirs(args.trace_dir, exist_ok=True)
            path = os.path.join(args.trace_dir, f"{req['id']}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"request": req, "mode": mode,
                           "run": {"status": result.status, "report": result.report,
                                   "trace": result.trace, "drafts": result.drafts}},
                          f, ensure_ascii=False, indent=2)
            print(f"trace 已写入 {path}")
        if result.report["status"] == "model_error" or result.status == "model_protocol_error":
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
