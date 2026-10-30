# 03 评估

## 测试预期与实际结果

测试命令：`python3 -m unittest discover -s tests -v`（32 个测试，实际输出见 `examples/test-results.txt`，全部通过）。
示例命令：`python3 -m src.run_examples --today 2026-10-30`（12 个请求，索引见 `examples/index.md`）。

| 验收点 | 预期 | 实际（证据文件） | 结果 |
|---|---|---|---|
| 正常排期 | 无风险、时段可用 → 保存确认草稿，published=false | `examples/traces/R1-01.trace.json`：M08@10:00 confirmed、M07@19:00 confirmed | 通过 |
| 素材信息缺失 | 不保存草稿，建议时段，标待确认 | `examples/traces/R2-01.trace.json`：M04 缺 publish_time，建议 19:00/20:00，not_saved | 通过 |
| 规则风险（阻断） | 极限词/竞品信息 → blocker，不排期不保存 | `examples/traces/R3-01.trace.json`（M02 第一）、`R3-02.trace.json`（M06 竞品发布会） | 通过 |
| 规则风险（提醒） | 合作方 → warning，草稿存为待确认 | `examples/traces/R4-01.trace.json`：M05 pending_confirmation | 通过 |
| 时段冲突（参考实例） | 18:00 占用 → 建议 19:00 并标记待确认，不改硬性时间，不称已发布 | `examples/traces/R4-02.trace.json`：D0001@18:00 pending_confirmation，suggest [19:00, 20:00] | 通过 |
| 时段冲突（非整点） | 19:30 不符合类型规则 → 建议 19:00，不改期 | `examples/traces/R4-03.trace.json` | 通过 |
| 重复提交 | 同一素材只保留一份草稿；再次提交返回 duplicate | `examples/traces/R5-01.trace.json`（首发）与 `R5-02.trace.json`（重复，duplicate_existing，draft_id 不变）；测试 `test_two_submissions_same_store_one_draft` | 通过 |
| 保存失败 | 明确返回失败，不虚报成功 | `examples/traces/R6-01.trace.json`：两次 save 均失败，draft_save_status=failed，claim_check 通过 | 通过 |
| 执行上限 | 超限返回明确未完成状态并列出未完成项 | `examples/traces/R7-01.trace.json`：incomplete_limit_reached，M08 not_processed | 通过 |
| 越权/虚报防御 | publish_now 被拒；虚报保存成功被标注 | 测试 `test_publish_claim_flagged`、`test_fake_save_success_flagged` | 通过 |

## 两条执行路径（依据工具结果调整动作）

- **冲突路径（R4-02）**：`read → check(ok) → query(occupied, 建议 19:00/20:00) → save_draft(18:00, pending_confirmation) → finish`。
  关键调整：观察到的占用信息使模型放弃「直接确认草稿」，改为建议替代并标记待确认，硬性时间保持不变。
- **失败重试路径（R6-01）**：`read → check → query(available) → save_draft(失败) → save_draft(重试失败) → finish`。
  关键调整：第一次保存失败后模型选择重试一次，仍失败后如实报告失败并停止，未虚报成功。
- 另有阻断路径（R3-01：`read → check(blocker) → finish`）与信息缺失路径（R2-01：`read → check → query → finish`）。

## 失败与限制

- 排期冲突仅按「同渠道」判定，跨渠道协同不在范围内；规则库为合成样例，真实业务需替换 `data/rules.json`。
- 草稿库为内存 + 快照，不处理并发写；多进程同时保存未做锁。
- 失败场景通过故障注入模拟（`faults.save_draft`），不是真实服务故障。
- mock 决策器为确定性实现，只证明流程与防御逻辑；真实模型在开放提示下可能选择次优动作，系统通过协议校验、上限与声明核对接住，但不保证最优排期。
- 非整点、跨天、重复主题等边界只做了最小覆盖（见测试与 examples）。
