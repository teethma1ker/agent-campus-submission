# examples/ 结构说明

| 路径 | 内容 |
|---|---|
| `index.md` | 一页式证据索引：12 个请求 → 场景/验收点/执行路径/关键结论（由脚本生成） |
| `requests/<ID>.request.json` | 每次运行实际使用的输入快照（素材、排期、规则、故障注入） |
| `traces/<ID>.trace.json` | 完整 Trace + 系统结果报告（逐步 thought/action/arguments/observation/decision_basis） |
| `traces/<ID>.console.txt` | CLI 实际输出（人读版） |
| `test-results.txt` | `python -m unittest discover -s tests -v` 的实际输出（32 个测试） |

覆盖：正常排期（R1-01/R1-02）、信息缺失（R2-01）、规则风险（R3-01/R3-02/R4-01）、
时段冲突（R4-02/R4-03，含参考实例）、重复提交（R5-01/R5-02）、保存失败（R6-01）、
执行上限未完成状态（R7-01）。

生成命令：`python -m src.run_examples --today 2026-10-30`（mock 模式）。
真实模型模式：同样命令加 `--model real`（需 `.env` 配置），结果写入 `examples/real-model/`，不覆盖本目录 mock 证据。
