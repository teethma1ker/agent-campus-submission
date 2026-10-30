# 证据索引（由 src/run_examples.py 生成）

运行日期：2026-10-30｜模型模式：`mock`｜请求数：12

| 请求 | 场景 | 验收点 | 状态 | 步数/工具 | 执行路径（模型动作序列） | 关键结论 |
|---|---|---|---|---|---|---|
| [R1-01](traces/R1-01.trace.json) | 正常排期 | 正常排期 | completed | 9/8 | `read_material → check_rules → query_slots → save_draft → read_material → check_rules → query_slots → save_draft → finish` | M08:ok/D0001@10:00(confirmed)/saved；M07:ok/D0002@19:00(confirmed)/saved；声明核对通过 |
| [R1-02](traces/R1-02.trace.json) | 正常排期（第二条执行路径） | 正常排期 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M16:ok/D0001@18:00(confirmed)/saved；声明核对通过 |
| [R2-01](traces/R2-01.trace.json) | 素材信息缺失 | 素材信息缺失 | completed | 4/3 | `read_material → check_rules → query_slots → finish` | M04:ok/无草稿/not_saved；声明核对通过 |
| [R3-01](traces/R3-01.trace.json) | 规则风险（极限词） | 规则风险 | completed | 3/2 | `read_material → check_rules → finish` | M02:blocker/无草稿/not_saved；声明核对通过 |
| [R3-02](traces/R3-02.trace.json) | 规则风险（竞品信息） | 规则风险 | completed | 3/2 | `read_material → check_rules → finish` | M06:blocker/无草稿/not_saved；声明核对通过 |
| [R4-01](traces/R4-01.trace.json) | 规则风险（合作方需法务确认） | 规则风险 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M05:warning/D0001@11:00(pending_confirmation)/saved；声明核对通过 |
| [R4-02](traces/R4-02.trace.json) | 时段冲突 | 时段冲突 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M01:ok/D0001@18:00(pending_confirmation)/saved；声明核对通过 |
| [R4-03](traces/R4-03.trace.json) | 时段冲突（非整点要求） | 时段冲突 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M03:ok/D0001@19:30(pending_confirmation)/saved；声明核对通过 |
| [R5-01](traces/R5-01.trace.json) | 重复提交（首发） | 重复提交 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M08:ok/D0001@10:00(confirmed)/saved；声明核对通过 |
| [R5-02](traces/R5-02.trace.json) | 重复提交（再次提交同一素材） | 重复提交 | completed | 5/4 | `read_material → check_rules → query_slots → save_draft → finish` | M01:ok/D0001@18:00(pending_confirmation)/duplicate_existing；声明核对通过 |
| [R6-01](traces/R6-01.trace.json) | 保存失败 | 保存失败 | completed | 6/5 | `read_material → check_rules → query_slots → save_draft → save_draft → finish` | M08:ok/无草稿/failed；声明核对通过 |
| [R7-01](traces/R7-01.trace.json) | 步数上限（未完成状态） | 执行上限 | incomplete_limit_reached | 3/3 | `read_material → check_rules → query_slots` | M01:ok/无草稿/not_saved；M08:not_processed/无草稿/not_saved；声明核对通过 |

每份 `traces/<ID>.trace.json` 含：请求输入快照、逐步 `thought/action/arguments/observation_summary`、
`decision_basis`（决策依据）、系统构建的 `report`（审核状态/风险/建议/待确认/草稿保存状态）。
控制台输出见同名 `.console.txt`；输入快照见 `requests/<ID>.request.json`。
