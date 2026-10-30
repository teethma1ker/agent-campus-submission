# 04 AI 使用说明

## AI 辅助内容

- 工程脚手架、四工具初版、ReAct 循环与 Mock 决策器的初版代码由 AI 生成。
- 文档 01–05 与 README 由 AI 起草，人工核对事实与数字。
- 合成数据（materials/schedule/rules/requests）由 AI 按题目要求构造。

## 个人修改与关键接管

- **重写决策层**：初版 Mock 是「预演整段动作列表」，不满足「根据工具结果调整」的要求；改为每步读取完整 Trace、按工具返回自适应决策（缺字段/阻断/占用/失败/重复分别走不同路径）。
- **修正报告构建**：初版报告部分字段取自模型自述；改为全部取自工具真实返回与草稿库状态，并增加声明核对（发现「未保存成功」被误判为虚报后，修正为只看最终总结并排除否定表述）。
- **规则细化**：合作方内容由 blocker 改为 warning（待法务确认），并让 warning 草稿存为 pending_confirmation；冲突改为按同渠道判定。
- **场景修正**：M03 调整为 19:30 非整点需求，用于覆盖「时间不合规」冲突；调整 R1/R5/R6 的素材分配，避免场景互相污染。

## 真实模型（DeepSeek）接入后的修订记录

1. **第一轮真实运行暴露缺陷**：R4-02（参考实例）中模型在工具明确返回 `occupied` 后，仍以 `status=confirmed` 保存草稿
   （见修订前 Trace）。仅靠提示词不可靠，因此在 `save_draft` 中加入**服务端一致性兜底**：
   时段被占/超营业时间/类型不合规时，无论模型传什么状态，服务端一律落库为 `pending_confirmation`，
   并在返回中附 `status_adjusted` 说明，报告同步展示「系统一致性调整」。
   新增 3 个测试（`ConsistencyGuardTest`），修订后 R4-02 真实 Trace 已正确输出待确认。
2. **第二轮真实运行**：R2-01 模型读取到缺 `publish_time` 后直接结束，未调用 `query_slots`，
   报告无法给出建议时段。按 Trace 收紧提示词：「信息缺失也必须先查规则、再查可用时段」。
   重跑后执行路径为 `read → check_rules → query_slots → finish`，审核状态与建议时段均完整。
3. **保留的行为差异**：R4-03（19:30 非整点）真实模型选择「不保存、直接请求确认」，Mock 选择「保存 19:30 待确认草稿」。
   两者都守住了确认边界（未改期、未发布），系统按实际 Trace 如实记录，不做一致性美化。

## 验证方式

- `python3 -m unittest discover -s tests -v`：32 个测试全部通过（见 `examples/test-results.txt`）。
- 12 个示例请求逐条人工核对 Trace：每步参数、观察摘要、后续动作、决策依据均可复现（`examples/index.md`）。
- 参考实例 M01 输出的关键核对：草稿时间为 18:00（硬性要求未改）、状态 pending_confirmation、建议 19:00/20:00、`published=false`、`claim_check.passed=true`。
- 真实模型证据：`examples/real-model/`（12 个请求的 Trace 与索引）；Mock 证据：`examples/`。两者结构一致、可对比。
