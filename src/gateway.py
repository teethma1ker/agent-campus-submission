"""模型网关：真实 LLM（OpenAI 兼容接口）与离线 Mock 决策器。

- LLMClient:   OpenAI 兼容 /chat/completions 接口（DeepSeek 等），带重试。
- MockLLM:     离线确定性决策器。边界：只验证流程与工具接线，
               不代表真实模型的语义理解与决策质量；输出格式与真实模式一致。
- 任何模型输出都会经过 agent.py 的协议解析与动作校验。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DEFAULT_DATE = "2026-10-31"


# ---------------------------------------------------------------- 配置
def load_dotenv(path: str = ".env") -> None:
    """极简 .env 解析；不覆盖已存在的环境变量。无需第三方依赖。"""
    import os

    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class GatewayConfig:
    mode: str = "mock"          # mock | real
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.0
    timeout: float = 60.0

    @staticmethod
    def from_env() -> "GatewayConfig":
        import os

        return GatewayConfig(
            mode=os.environ.get("MODEL_MODE", "mock").strip().lower(),
            base_url=os.environ.get("MODEL_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.environ.get("MODEL_NAME", "deepseek-chat").strip(),
            api_key_env=os.environ.get("MODEL_API_KEY_ENV", "DEEPSEEK_API_KEY").strip(),
            temperature=float(os.environ.get("MODEL_TEMPERATURE", "0") or 0),
        )


# ---------------------------------------------------------------- Prompt（真实模式）
SYSTEM_PROMPT = """你是内容团队的「素材审核与发布排期助手」，在只读审核与草稿层面工作。

# 可用工具（每次只调用一个动作）
1. read_material{"material_id":"M01"}          读取素材元数据
2. check_rules{"material_id":"M01"}            按 content_rules 检查风险（返回 blocker/warning/ok）
3. query_slots{"material_id":"M01"}            查询与素材同渠道、格式相容、营业时段内且未被占用的时段
4. save_draft{"material_id":"M01","slot":"19:00"}  仅保存排期草稿（不发布！）

# 硬性约束
- publish_time 是用户硬性要求：不得把草稿保存到与其不同的时段。同渠道碰撞时只能标记待确认，等用户决定。
- 你只做三件事：审核、建议、存草稿。绝不发布、绝不虚报成功；save_draft 返回什么就如实记录什么。
- 工具返回 error/blocked unconfirmed=false 时，先自行补查或换策略，不要立刻停下。

# 每个素材的决策流程（基于工具结果动态推进）
- 先 read_material。
- 若 publish_time 为空：只能建议“常规时段”草稿（不保存）并请求用户确认时间。
- publish_time 已知：接着 check_rules。
  * blocker → 不保存草稿，标记阻断，向用户报告风险原因。
  * warning → 记录待确认项；时段可用时仍可保存，但草稿状态必须为 pending_confirmation（等待人工复核）。
  * ok → 继续 query_slots。
- query_slots 结果：
  * 硬性时段 occupied：不得改期。向用户提出建议替代时段并请求确认，随后仍以“用户的 publish_time 时段+待确认”调用 save_draft 保存草稿（draft_status=pending_confirmation）。
  * 超出营业时间或格式不合规（非 blocker）：建议替代时段并请求用户确认，不得直接改期保存。
  * 可用且 check_rules 为 ok：以 publish_time 调用 save_draft，status=confirmed。
- save_draft 返回 duplicate=true：如实告知该素材已有草稿，不重复保存。
- save_draft 返回 error：先重试一次（可能为瞬时故障）；仍失败则明确报告保存失败，不得声称已保存。
- 全部处理后结束。

# 输出格式：只输出一个 JSON 对象，不要使用 markdown 代码块
{"thought":"一句话说明当前判断","action":"read_material|check_rules|query_slots|save_draft|finish","arguments":{...}}
结束时：{"thought":"...","action":"finish","arguments":{"summary":"<一句话总结>"}}
"""


def render_user_prompt(request: dict, trace: list[dict], today: date) -> str:
    d = date.fromisoformat(request.get("date", DEFAULT_DATE))
    lines = [
        f"今天：{today.isoformat()}（星期{'一二三四五六日'[today.weekday()]}）",
        f"排期日期：{d.isoformat()}（星期{'一二三四五六日'[d.weekday()]}）",
        f"渠道：{request['channel']}　主题：{request['theme']}　需求：{request['request']}",
        "",
        "## 本批待处理素材（简要）",
    ]
    for m in request["materials"]:
        f = m["fields"]
        brief = "；".join(f"{k}={v}" for k, v in f.items() if k not in ("title", "description"))
        lines.append(f"- {m['id']}：{brief}｜{m['description']}")
    lines += ["", "## 已保存草稿"]
    if request.get("existing_drafts"):
        for dr in request["existing_drafts"]:
            lines.append(f"- {dr['material_id']} @ {dr['slot']}（{dr['status']}，草稿号 {dr['draft_id']}）")
    else:
        lines.append("- （无）")
    lines += ["", "## 已有排期（全部渠道）"]
    for o in request.get("schedule", {}).get("occupied", []):
        lines.append(f"- {o['slot']} ← {o['material_id']} {o['title']}")
    for b in request.get("schedule", {}).get("blocked_slots", []):
        lines.append(f"- {b['slot']} 禁排：{b['reason']}")
    lines += ["", "## 内容规则摘要（完整规则由 check_rules/query_slots 返回）"]
    rules = request.get("rules", {})
    lines.append(f"- 营业时间 {rules.get('business_hours', {}).get('start')}–{rules.get('business_hours', {}).get('end')}")
    lines.append(f"- 类型时段限制：{json.dumps(rules.get('type_slot_rules', []), ensure_ascii=False)}")
    lines.append(f"- 敏感实体：{json.dumps(rules.get('entity_rules', []), ensure_ascii=False)}")
    lines.append(f"- 极限词：{json.dumps([t.get('term') for t in rules.get('blocked_terms', [])], ensure_ascii=False)}")
    lines += ["", f"## 执行轨迹（最多最近 30 步；已用 {len(trace)} 步）"]
    for st in trace[-30:]:
        lines.append(
            f"[{st['step']}] {st['action']} 参数={json.dumps(st['arguments'], ensure_ascii=False)}"
            f" → {st['observation_summary']}"
        )
    if not trace:
        lines.append("（尚无动作，请给出第一步）")
    lines += ["", "请输出下一个动作（JSON）。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 真实 LLM 客户端
class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg

    def _key(self) -> str:
        import os

        key = os.environ.get(self.cfg.api_key_env, "").strip()
        if not key:
            raise LLMError(f"缺少环境变量 {self.cfg.api_key_env}（或在 .env 中配置）")
        return key

    def complete(self, system: str, user: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key()}"},
        )
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                snippet = e.read().decode("utf-8", "ignore")[:300]
                last_err = LLMError(f"HTTP {e.code}: {snippet}")
                if e.code in (400, 401, 403, 404):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as e:
                last_err = LLMError(str(e))
            time.sleep(1.5 * (attempt + 1))
        raise last_err or LLMError("模型调用失败")


# ---------------------------------------------------------------- JSON 解析（宽松）
def extract_json(text: str) -> dict:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    if fence:
        stripped = fence.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出中没有 JSON 对象")
    return json.loads(stripped[start:end + 1])


# ---------------------------------------------------------------- 离线 Mock 决策器
class MockLLM:
    """离线确定性决策器（与真实模式共用同一 request/trace，接口为 decide()）。

    - 每一步只根据“已经观察到的工具返回”决定下一步：先 read → 依 missing 决定是否
      check/query；依 check 的 blocker/warning 决定是否继续；依 query 的时段状态决定
      保存策略与确认状态；依 save 的 ok/duplicate/error 决定重试或终止。
    - 与真实模式输出同一 JSON 决策协议。
    - 边界：只验证流程与工具接线，不证明真实模型的语义理解与决策质量。
    """

    TOOLS = ("read_material", "check_rules", "query_slots", "save_draft")

    def decide(self, request: dict, trace: list[dict]) -> dict:
        for item in request["materials"]:
            mid = item["id"]
            acts = [t for t in trace
                    if t.get("arguments", {}).get("material_id") == mid and t["action"] in self.TOOLS]
            kinds = [t["action"] for t in acts]
            obs = {}
            for t in acts:
                obs.setdefault(t["action"], []).append(t.get("observation") or {})

            # 1) 读取
            if "read_material" not in kinds:
                return self._act("先读取素材，确认关键字段是否齐全。", "read_material", {"material_id": mid})
            read = obs["read_material"][0]
            if read.get("error"):
                continue  # 素材不存在等：跳过，交由报告记录
            missing = read.get("missing_fields") or []

            # 2) 规则检查（任何素材都要看风险）
            if "check_rules" not in kinds:
                return self._act("素材已读取，继续按内容规则检查风险。", "check_rules", {"material_id": mid})
            check = obs["check_rules"][0]

            # 3) 资料不足：查询可用时段用于“建议”，但不保存草稿
            if missing:
                if "query_slots" not in kinds:
                    return self._act(
                        f"{mid} 缺少 {'/'.join(missing)}，无法确定硬性时段；先查可用时段以便给出建议（不保存）。",
                        "query_slots", {"material_id": mid})
                continue  # 该素材处理完毕（待用户补资料）

            # 4) 阻断级风险：不排期
            if check.get("status") == "blocker":
                continue

            # 5) 时段查询
            if "query_slots" not in kinds:
                return self._act("规则未阻断，查询该素材可用时段。", "query_slots", {"material_id": mid})
            query = obs["query_slots"][0]
            req = query.get("requested_slot") or {}
            pt = read.get("publish_time")
            hard = read.get("required", True)
            saves = obs.get("save_draft", [])
            last_save = saves[-1] if saves else None

            if req.get("status") == "available":
                if last_save is None:
                    if check.get("status") == "warning":
                        return self._act(
                            f"{mid} 时段 {pt} 可用，但存在规则提醒（{'；'.join(check.get('reasons') or [])}），"
                            f"草稿保存为待确认，等待人工复核。", "save_draft",
                            {"material_id": mid, "slot": pt, "status": "pending_confirmation"})
                    return self._act(
                        f"{mid} 硬性时段 {pt} 可用且无风险，保存确认草稿。", "save_draft",
                        {"material_id": mid, "slot": pt, "status": "confirmed"})
                if last_save.get("ok") or last_save.get("duplicate"):
                    continue
                if len(saves) == 1:
                    retry_status = "pending_confirmation" if check.get("status") == "warning" else "confirmed"
                    return self._act(
                        f"保存失败（{last_save.get('error')}），先重试一次相同保存请求以排查瞬时故障。",
                        "save_draft", {"material_id": mid, "slot": pt, "status": retry_status})
                continue  # 重试仍失败：如实向用户报告，不虚报

            # 6) 用户硬性时段不可用
            why = req.get("reason") or req.get("status")
            alts = query.get("suggested_slots") or []
            alt_text = "/".join(alts) if alts else "暂无"
            if hard:
                if last_save is None:
                    return self._act(
                        f"{mid}：{why}；不得擅自改期。建议替代时段 {alt_text} 并请求用户确认；"
                        f"沿用用户硬性时段保存草稿并标记待确认。",
                        "save_draft", {"material_id": mid, "slot": pt, "status": "pending_confirmation"})
                if last_save.get("ok") or last_save.get("duplicate"):
                    continue
                if len(saves) == 1:
                    return self._act(
                        f"保存失败（{last_save.get('error')}），重试一次；仍失败则报告用户。",
                        "save_draft", {"material_id": mid, "slot": pt, "status": "pending_confirmation"})
                continue
            # 柔性时段：不保存，给建议即可
            continue

        return {"thought": "所有素材已处理完毕，按各步工具返回汇总结果。", "action": "finish",
                "arguments": {"summary": self._finish_summary(request, trace)}}

    def _finish_summary(self, request: dict, trace: list[dict]) -> str:
        parts = []
        for item in request["materials"]:
            mid = item["id"]
            acts = [t for t in trace
                    if t.get("arguments", {}).get("material_id") == mid and t["action"] in self.TOOLS]
            if not acts:
                parts.append(f"{mid} 未处理")
                continue
            read = next((t["observation"] for t in acts if t["action"] == "read_material"), {}) or {}
            check = next((t["observation"] for t in acts if t["action"] == "check_rules"), {}) or {}
            query = next((t["observation"] for t in acts if t["action"] == "query_slots"), {}) or {}
            saves = [t["observation"] for t in acts if t["action"] == "save_draft"]
            if read.get("error"):
                parts.append(f"{mid} 读取失败：{read['error']}")
            elif read.get("missing_fields"):
                sug = "/".join(query.get("suggested_slots") or []) or "待定"
                parts.append(f"{mid} 缺 {'/'.join(read['missing_fields'])}，未保存草稿，建议时段 {sug}（待用户确认）")
            elif check.get("status") == "blocker":
                parts.append(f"{mid} 被阻断（{'；'.join(check.get('reasons') or [])}），未进入排期")
            else:
                last = saves[-1] if saves else None
                note = ""
                if check.get("status") == "warning":
                    note = f"，风险提醒：{'；'.join(check.get('reasons') or [])}（待人工复核）"
                if last is None:
                    parts.append(f"{mid} 未保存草稿{note}")
                elif last.get("duplicate"):
                    parts.append(f"{mid} 已有草稿 {last.get('draft_id')}@{last.get('slot')}，未重复保存{note}")
                elif last.get("ok"):
                    parts.append(f"{mid} 草稿 {last.get('draft_id')}@{last.get('slot')}（{last.get('status')}）已保存{note}")
                else:
                    parts.append(f"{mid} 草稿保存失败：{last.get('error')}（本次未保存成功）")
        return "；".join(parts) + "。全程仅保存草稿，未发布任何内容。"

    @staticmethod
    def _act(thought: str, action: str, args: dict) -> dict:
        return {"thought": thought, "action": action, "arguments": args}


def build_gateway(mode: str):
    """返回模型网关。mock=离线决策器；real=真实 LLM（缺 key 时给出清晰错误）。"""
    if mode == "mock":
        return MockLLM()
    return LLMClient(GatewayConfig.from_env())
