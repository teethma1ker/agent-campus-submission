# 素材审核与发布排期 Agent

内容团队的素材审核与排期助手：读取素材元数据、按规则查风险、查可用时段、给出建议并**只保存草稿**。
模型在每一步根据工具真实返回决定下一步（补查 / 换时段 / 请求确认 / 阻断 / 保存 / 重试 / 结束），
有步数与工具调用上限，越权发布或虚报保存结果会被系统标注。

## 1. 环境与安装

- Python ≥ 3.10，**仅用标准库**，无需安装第三方依赖。
- 默认 `mock` 模式离线可跑；真实模型模式需一个 OpenAI 兼容接口（已在 DeepSeek `deepseek-chat` 上验证）。

```bash
git clone <repo-url> && cd agent-campus-submission
python3 --version            # 3.10+
python3 -m unittest discover -s tests -v      # 32 个测试，全部应通过
```

## 2. 运行（CLI，三选一中的 CLI 方案）

```bash
# ① 查看待处理素材总览
python3 -m src.cli

# ② 运行单个场景（题目参考实例：M01 要求 18:00 已占用、19:00 可用 → 建议 19:00 待确认）
python3 -m src.cli --requests R4-02

# ③ 运行全部 12 个示例请求并打印报告
python3 -m src.cli --all

# ④ 重生成全部证据（Trace / 请求快照 / 索引）
python3 -m src.run_examples --today 2026-10-30
```

真实模型模式：

```bash
cp .env.example .env     # 填入真实密钥（.env 已被 .gitignore 排除）
# 编辑 .env：MODEL_MODE=real，并设置 DEEPSEEK_API_KEY=...
python3 -m src.cli --requests R4-02 --model real
python3 -m src.run_examples --model real   # 结果写入 examples/real-model/
```

离线 Mock 边界：`mock` 是确定性决策器，只验证流程、工具接线与防御逻辑，**不证明真实模型的语义理解与决策质量**；真实模式与它共用同一循环、同一工具、同一份输入。

## 3. 评审入口

- 一页式证据索引：`examples/index.md`（12 个请求 → 场景/验收点/执行路径/关键结论）
- 参考实例（18:00 冲突 → 19:00 待确认）：`examples/traces/R4-02.trace.json`
- 六类验收点与文件对应关系：`docs/03-evaluation.md`
- 测试实际输出：`examples/test-results.txt`

## 4. 目录

```
src/        gateway.py(模型接入: 真实 LLM + 离线决策器)  tools.py(四工具)  agent.py(ReAct 循环/上限/防虚报)
            cli.py(命令行)  run_examples.py(批量证据生成)
tests/      test_tools.py(工具层)  test_scenarios.py(六类验收+上限+防御)
examples/   traces/ requests/ index.md test-results.txt   （mock 证据）
data/       materials.json schedule.json rules.json requests.json （合成资料与规则）
docs/       01-requirements ~ 05-retrospective
```

## 5. 已知限制（详见 docs/03-evaluation.md）

- 排期冲突按「同渠道」判定（不同平台可同时段并行），规则可配置；
- 草稿库为内存 + 文件快照，不做并发写锁；
- 发布能力被有意禁用（`published` 恒为 false）；
- mock 模式不代表真实模型效果；真实模型输出质量依赖所选模型。

仓库内全部为合成数据，不含任何真实密钥或个人隐私。
