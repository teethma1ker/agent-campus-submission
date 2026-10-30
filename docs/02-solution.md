# 02 方案设计

## 架构

```
CLI --requests <ID>
  └─ cli.py 构建输入快照（素材/排期/规则/预置草稿/故障注入）
      └─ agent.py  ReAct 循环（上限：步数、工具调用、协议错误）
          ├─ gateway.py  真实 LLM（OpenAI 兼容） 或 离线 Mock 决策器（同一 request+trace 接口）
          ├─ tools.py    read_material / check_rules / query_slots / save_draft
          └─ 报告构建器（以工具真实返回 + 草稿库真实状态为准；声明核对）
```

## 调用流程与动态决策

每一轮：模型看到「请求 + 已有 Trace」→ 输出一个 JSON 动作 → 系统校验参数 → 执行工具 → 回填观察（observation）→ 模型决定下一步或结束。核心决策点全部由**工具返回内容**触发：

| 工具结果 | 模型的下一个动作 |
|---|---|
| read_material 返回 `missing_fields` | 不再保存草稿，查可用时段作为建议，结束并请求补资料 |
| check_rules 返回 `blocker` | 不进入排期，不保存草稿，结束并报告阻断原因 |
| check_rules 返回 `warning` | 继续保存，但草稿状态转为 `pending_confirmation`（等人工复核） |
| query_slots 返回 `occupied`（硬性时段） | 不擅自改期；建议最近的替代时段；保存「原时间 + 待确认」草稿 |
| query_slots 返回 `format_mismatch` / `blocked` | 同上：建议替代时段 + 待确认，不改期 |
| save_draft 返回 `error` | 重试一次；仍失败则如实报告保存失败，不得声称成功 |
| save_draft 返回 `duplicate` | 如实报告已有草稿，不重复创建 |

两条典型执行路径（见 `examples/index.md`）：
- `R4-02`：`read → check(ok) → query(occupied) → save_draft(18:00, pending_confirmation) → finish`（冲突路径）
- `R3-01`：`read → check(blocker) → finish`（阻断路径，不保存）
- `R6-01`：`read → check → query → save(失败) → save(重试失败) → finish`（失败重试路径）

## 技术取舍

- **不依赖框架**（原生 Python + JSON 动作协议）：题目明确框架名称不加分，原生实现让「动态决策」与「防御逻辑」更透明、可测试；四工具与循环代码量可控。
- **CLI 而非 HTTP/Notebook**：题目三选一，CLI 最贴近运营排期操作且易复现（一条命令一个场景）。
- **真实模型 + 离线 Mock 双网关**：Mock 是确定性决策器（基于工具返回值自适应），保证离线可评审；真实模式共用同一循环与工具，仅替换决策来源。Mock 边界在 README/04 中明确标注。
- **报告由工具真值构建**：模型自述不可信——报告字段全部来自工具返回与草稿库状态；另设「声明核对」检查模型文本中的「已发布/保存成功」类虚报。
- **服务端一致性兜底**：`save_draft` 不信任模型传入的 `status`——若目标时段被占用/超营业时间/类型不合规，一律落库为 `pending_confirmation` 并返回 `status_adjusted`；（真实模型首轮运行曾把冲突草稿存成 confirmed，该兜底正是为此设计）。
- **冲突按渠道判定**：不同平台（视频号/公众号/小红书）同一时段可并行，同渠道才算占用；规则与数据分离，便于调参。
- **幂等草稿**：以 material_id 为键，重复提交返回 `duplicate=true` 且不覆盖原草稿；故障注入用于验证保存失败与重试。
- **上限**：步数 20 / 工具调用 16 / 协议错误 5（可用 CLI 参数或请求级 `limit_steps` 覆盖），超限返回 `incomplete_limit_reached`。
