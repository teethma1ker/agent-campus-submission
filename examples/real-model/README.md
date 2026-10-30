# 真实模型证据（DeepSeek deepseek-chat）

- 生成命令：`python -m src.run_examples --model real --out examples/real-model --today 2026-10-30`
- 12 个请求与 `examples/`（Mock）一一对应，可逐条对比执行路径与结论。
- 运行中发现并修复的两个真实模型缺陷，见 `docs/04-ai-usage.md` 与 `docs/03-evaluation.md`：
  1. R4-02 首轮把冲突草稿存成 confirmed → 新增 `save_draft` 服务端一致性兜底（`status_adjusted`），已修正；
  2. R2-01 首轮跳过时段查询 → 收紧系统提示词，已修正。
- R4-03 保留了真实模型与 Mock 的合理行为差异（不保存无效时段草稿 vs 保存待确认草稿），两者均未越权。
