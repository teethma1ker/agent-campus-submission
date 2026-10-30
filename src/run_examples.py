"""批量运行 data/requests.json 中的示例请求，产出可评审的证据。

产出目录（默认 examples/）：
  traces/<请求ID>.trace.json    完整执行轨迹（含每步参数、返回摘要、决策依据）
  traces/<请求ID>.report.json   系统构建的最终结果报告（以工具真值为准）
  traces/<请求ID>.console.txt   CLI 控制台输出
  requests/<请求ID>.request.json 本次运行实际使用的输入
  outputs/index.md              一页式证据索引（场景 → 文件 → 关键结论）

用法：
  python -m src.run_examples                    # mock 模式（离线）
  python -m src.run_examples --model real       # 真实模型（读取 .env 配置）
  python -m src.run_examples --requests R4-02   # 只跑指定请求
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent, Limits  # noqa: E402
from cli import load_requests, make_request_payload, print_result  # noqa: E402
from gateway import build_gateway, load_dotenv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    load_dotenv(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", help="逗号分隔的请求 ID；缺省跑全部")
    ap.add_argument("--model", choices=["mock", "real", "auto"], default="mock")
    ap.add_argument("--out", default=None,
                    help="输出目录；默认 mock→examples/，real→examples/real-model/（互不覆盖）")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--index-only", action="store_true",
                    help="只根据 --out 下已有 trace 重建 index.md（不重新调用模型）")
    args = ap.parse_args()

    if args.out is None:
        args.out = os.path.join(ROOT, "examples" if args.model != "real" else "examples/real-model")
    reqs = load_requests()
    if args.requests:
        wanted = {s.strip() for s in args.requests.split(",") if s.strip()}
        reqs = [r for r in reqs if r["id"] in wanted]
    os.makedirs(os.path.join(args.out, "traces"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "requests"), exist_ok=True)
    today = date.fromisoformat(args.today)

    index_rows = []
    for req in reqs:
        trace_path = os.path.join(args.out, "traces", f"{req['id']}.trace.json")
        if args.index_only:
            try:
                t = json.load(open(trace_path, encoding="utf-8"))
            except OSError:
                print(f"[{req['id']}] 缺少 trace，跳过")
                continue
            verdicts = []
            for e in t["report"]["materials"]:
                drafts = "无草稿" if not e["draft"] else f"{e['draft']['draft_id']}@{e['draft']['slot']}({e['draft']['status']})"
                verdicts.append(f"{e['material_id']}:{e['audit_status']}/{drafts}/{e['draft_save_status']}")
            index_rows.append({
                "id": req["id"], "scenario": req["scenario"], "acceptance": req.get("acceptance", []),
                "status": t["status"], "steps": t["report"]["steps"], "tool_calls": t["report"]["tool_calls"],
                "verdicts": verdicts, "claim_check": t["report"]["claim_check"],
                "path": " → ".join(s["action"] for s in t["trace"]),
            })
            continue
        payload, ctx = make_request_payload(req)
        with open(os.path.join(args.out, "requests", f"{req['id']}.request.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        gateway = build_gateway(args.model)
        limits = Limits(max_steps=req.get("limit_steps", 20), max_tool_calls=req.get("limit_tools", 16))
        agent = Agent(gateway, ctx, limits=limits, today=today)
        result = agent.run_request(payload)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_result(req, result, args.model)
        console = buf.getvalue()
        with open(os.path.join(args.out, "traces", f"{req['id']}.console.txt"), "w", encoding="utf-8") as f:
            f.write(console)

        run = {"request_id": req["id"], "scenario": req["scenario"], "mode": args.model,
               "run_date": today.isoformat(), "status": result.status,
               "report": result.report, "trace": result.trace, "drafts": result.drafts}
        with open(os.path.join(args.out, "traces", f"{req['id']}.trace.json"), "w", encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False, indent=2)

        verdicts = []
        for e in result.report["materials"]:
            drafts = "无草稿" if not e["draft"] else f"{e['draft']['draft_id']}@{e['draft']['slot']}({e['draft']['status']})"
            verdicts.append(f"{e['material_id']}:{e['audit_status']}/{drafts}/{e['draft_save_status']}")
        index_rows.append({
            "id": req["id"], "scenario": req["scenario"], "acceptance": req.get("acceptance", []),
            "status": result.status, "steps": result.report["steps"],
            "tool_calls": result.report["tool_calls"], "verdicts": verdicts,
            "claim_check": result.report["claim_check"],
            "path": " → ".join(t["action"] for t in result.trace),
        })
        print(f"[{req['id']}] {req['scenario']} → {result.status}｜" + "；".join(verdicts))

    # ---- 一页式索引
    lines = [
        "# 证据索引（由 src/run_examples.py 生成）",
        "",
        f"运行日期：{today.isoformat()}｜模型模式：`{args.model}`｜请求数：{len(index_rows)}",
        "",
        "| 请求 | 场景 | 验收点 | 状态 | 步数/工具 | 执行路径（模型动作序列） | 关键结论 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in index_rows:
        claim = "声明核对通过" if row["claim_check"]["passed"] else "声明核对未通过：" + "；".join(row["claim_check"]["issues"])
        lines.append(
            f"| [{row['id']}](traces/{row['id']}.trace.json) | {row['scenario']} | "
            f"{'、'.join(row['acceptance'])} | {row['status']} | {row['steps']}/{row['tool_calls']} | "
            f"`{row['path']}` | {'；'.join(row['verdicts'])}；{claim} |")
    lines += [
        "",
        "每份 `traces/<ID>.trace.json` 含：请求输入快照、逐步 `thought/action/arguments/observation_summary`、",
        "`decision_basis`（决策依据）、系统构建的 `report`（审核状态/风险/建议/待确认/草稿保存状态）。",
        "控制台输出见同名 `.console.txt`；输入快照见 `requests/<ID>.request.json`。",
    ]
    with open(os.path.join(args.out, "outputs", "index.md") if os.path.isdir(os.path.join(args.out, "outputs"))
              else os.path.join(args.out, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n证据已写入 {args.out}/（traces/ requests/ index.md）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
