# 证据索引（由 src/run_examples.py 生成）

运行日期：2026-10-30｜模型模式：`real`｜请求数：1

| 请求 | 场景 | 验收点 | 状态 | 步数/工具 | 执行路径（模型动作序列） | 关键结论 |
|---|---|---|---|---|---|---|
| [R2-01](traces/R2-01.trace.json) | 素材信息缺失 | 素材信息缺失 | completed | 4/3 | `read_material → check_rules → query_slots → finish` | M04:ok/无草稿/not_saved；声明核对通过 |

每份 `traces/<ID>.trace.json` 含：请求输入快照、逐步 `thought/action/arguments/observation_summary`、
`decision_basis`（决策依据）、系统构建的 `report`（审核状态/风险/建议/待确认/草稿保存状态）。
控制台输出见同名 `.console.txt`；输入快照见 `requests/<ID>.request.json`。
