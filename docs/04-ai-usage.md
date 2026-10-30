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

## 验证方式

- `python3 -m unittest discover -s tests -v`：32 个测试全部通过（见 `examples/test-results.txt`）。
- 12 个示例请求逐条人工核对 Trace：每步参数、观察摘要、后续动作、决策依据均可复现（`examples/index.md`）。
- 参考实例 M01 输出的关键核对：草稿时间为 18:00（硬性要求未改）、状态 pending_confirmation、建议 19:00/20:00、`published=false`、`claim_check.passed=true`。
