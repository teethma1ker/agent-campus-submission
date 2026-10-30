"""业务工具：read_material / check_rules / query_slots / save_draft。

全部为本地实现（合成数据 + 文件草稿库），无网络依赖。
- 时段查询：只考虑与素材同渠道的现有排期冲突（渠道之间互不影响）。
- 规则检查：极限词与竞品信息为硬性规则；合作方为需人工确认的合作规则。
- save_draft：幂等（同一素材已有草稿不重复写），并支持故障注入以验证“保存失败”场景。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime

ALL = "ALL"


# ---------------------------------------------------------------- 上下文
def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_context(data_dir: str = "data") -> dict:
    ctx = {
        "materials": {m["id"]: m for m in load_json(os.path.join(data_dir, "materials.json"))},
        "schedule": load_json(os.path.join(data_dir, "schedule.json")),
        "rules": load_json(os.path.join(data_dir, "rules.json")),
        "draft_store": {},        # material_id -> draft（幂等存储）
        "sequences": 0,           # 草稿流水号
        "faults": {"save_draft": None},  # {"error": msg, "remaining": n}
    }
    return ctx


# ---------------------------------------------------------------- 通用
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def hhmm(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not TIME_RE.match(s):
        return None
    return s


def minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def material_missing_fields(mat: dict) -> list[str]:
    missing = []
    if not mat.get("publish_time"):
        missing.append("publish_time")
    if not mat.get("publish_due"):
        missing.append("publish_due")
    if not mat.get("owner"):
        missing.append("owner")
    if not (mat.get("description") or "").strip():
        missing.append("description")
    return missing


def business_slots(rules: dict) -> list[str]:
    start = minutes(rules["business_hours"]["start"])
    end = minutes(rules["business_hours"]["end"])
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(start, end + 1, 60)]


def allowed_slots(rules: dict, fmt: str) -> list[str] | None:
    slots = business_slots(rules)
    for rule in rules.get("type_slot_rules", []):
        if rule["format"] == fmt:
            if rule["slots"] == ALL:
                return slots
            return [s for s in slots if s in set(rule["slots"])]
    return slots


def slot_record(ctx: dict, slot: str) -> dict | None:
    for o in ctx["schedule"].get("occupied", []):
        if o["slot"] == slot:
            return o
    return None


def collision(ctx: dict, mat: dict, slot: str) -> dict | None:
    """同渠道（同平台）时段占用才视为真实冲突；不同渠道可同点并行。"""
    o = slot_record(ctx, slot)
    if o is None:
        return None
    owner_mat = ctx["materials"].get(o["material_id"], {})
    if owner_mat.get("channel") == mat.get("channel"):
        return {**o, "channel": owner_mat.get("channel")}
    return None


# ---------------------------------------------------------------- 工具 1：读取素材
def build_read_material(ctx: dict):
    def read_material(material_id: str) -> str:
        mat = ctx["materials"].get(material_id)
        if not mat:
            return json.dumps({"error": f"素材 {material_id} 不存在", "tool": "read_material"}, ensure_ascii=False)
        return json.dumps({
            "material_id": material_id,
            "title": mat.get("title"),
            "format": mat.get("format"),
            "channel": mat.get("channel"),
            "theme": mat.get("theme"),
            "publish_time": mat.get("publish_time"),
            "publish_due": mat.get("publish_due"),
            "owner": mat.get("owner"),
            "required": bool(mat.get("required", True)),
            "description": mat.get("description"),
            "missing_fields": material_missing_fields(mat),
        }, ensure_ascii=False)
    return read_material


# ---------------------------------------------------------------- 工具 2：规则检查
def build_check_rules(ctx: dict):
    rules = ctx["rules"]

    def check_rules(material_id: str) -> str:
        mat = ctx["materials"].get(material_id)
        if not mat:
            return json.dumps({"error": f"素材 {material_id} 不存在", "tool": "check_rules"}, ensure_ascii=False)
        text = f"{mat.get('title', '')} {mat.get('description', '')}"
        hard, review = [], []

        for term_rule in rules.get("blocked_terms", []):
            if term_rule["term"] in text:
                hard.append(f"{term_rule['reason']}（命中：{term_rule['term']}）")

        for ent in rules.get("entity_rules", []):
            if ent["entity"] in text:
                (hard if ent["level"] == "blocker" else review).append(ent["message"])

        # 发布要求的时间格式是否合法也是规则检查的一部分
        pt = mat.get("publish_time")
        if pt and hhmm(pt) is None:
            hard.append(f"发布时间格式不规范：{pt!r}（应为 HH:MM）")

        status = "blocker" if hard else ("warning" if review else "ok")
        return json.dumps({
            "material_id": material_id,
            "status": status,
            "reasons": hard + review,
            "hard_issues": hard,
            "review_issues": review,
        }, ensure_ascii=False)
    return check_rules


# ---------------------------------------------------------------- 工具 3：时段查询
def build_query_slots(ctx: dict, today: date):
    rules = ctx["rules"]

    def query_slots(material_id: str) -> str:
        mat = ctx["materials"].get(material_id)
        if not mat:
            return json.dumps({"error": f"素材 {material_id} 不存在", "tool": "query_slots"}, ensure_ascii=False)
        fmt = mat.get("format", "")
        channel = mat.get("channel", "")
        pt = hhmm(mat.get("publish_time"))
        blocked = {b["slot"]: b["reason"] for b in ctx["schedule"].get("blocked_slots", [])}
        allowed = allowed_slots(rules, fmt) or []

        def slot_status(slot: str) -> dict:
            if slot in blocked:
                return {"slot": slot, "status": "blocked", "reason": blocked[slot]}
            if slot not in allowed:
                return {"slot": slot, "status": "format_mismatch",
                        "reason": f"{fmt}类内容不允许在 {slot} 投放（规则：{'/'.join(allowed)}）"}
            col = collision(ctx, mat, slot)
            if col:
                return {"slot": slot, "status": "occupied",
                        "reason": f"同渠道（{channel}）已有 {col['material_id']} {col['title']}"}
            return {"slot": slot, "status": "available", "reason": ""}

        if pt:
            requested = slot_status(pt)
            requested["hard"] = bool(mat.get("required", True))
        else:
            requested = {"slot": None, "status": "missing_time",
                         "reason": "素材缺少发布时间要求，无法检查硬性冲突", "hard": None}

        candidates = [s for s in allowed if slot_status(s)["status"] == "available"]
        if pt:  # 建议时段：离用户要求的硬性时间越近越优先
            candidates = sorted(candidates, key=lambda s: abs(minutes(s) - minutes(pt)))
        return json.dumps({
            "material_id": material_id,
            "format": fmt,
            "channel": channel,
            "date": today.isoformat(),
            "requested_slot": requested,
            "available_slots": candidates,
            "suggested_slots": candidates[:2],
        }, ensure_ascii=False)
    return query_slots


# ---------------------------------------------------------------- 工具 4：保存草稿
def build_save_draft(ctx: dict):
    def save_draft(material_id: str, slot: str, status: str = "confirmed") -> str:
        # 故障注入（测试/示例用）：模拟文件系统或下游服务失败
        fault = ctx["faults"].get("save_draft")
        if fault and fault.get("remaining", 0) > 0:
            fault["remaining"] -= 1
            return json.dumps({
                "material_id": material_id, "ok": False,
                "error": fault.get("error", "草稿存储不可用：本地草稿库写入失败（EACCES: 草稿目录不可写）"),
            }, ensure_ascii=False)

        mat = ctx["materials"].get(material_id)
        if not mat:
            return json.dumps({"material_id": material_id, "ok": False, "error": f"素材 {material_id} 不存在"}, ensure_ascii=False)
        slot = hhmm(slot)
        if slot is None:
            return json.dumps({"material_id": material_id, "ok": False, "error": f"时段格式不规范：{slot!r}"}, ensure_ascii=False)
        if status not in ("confirmed", "pending_confirmation"):
            status = "pending_confirmation"

        # 服务端一致性：请求时段不可用/超营业时间/类型不合规时，草稿只能是待确认
        # （模型传入的 status 不可信；这里按规则核验，避免把冲突的确认草稿写进库）
        allowed = allowed_slots(ctx["rules"], mat.get("format", "")) or []
        blocked_slots = {b["slot"] for b in ctx["schedule"].get("blocked_slots", [])}
        slot_unusable = (slot not in allowed or slot in blocked_slots
                         or collision(ctx, mat, slot) is not None)
        requested_status = status
        if slot_unusable and status != "pending_confirmation":
            status = "pending_confirmation"

        existing = ctx["draft_store"].get(material_id)
        if existing:
            return json.dumps({
                "material_id": material_id, "ok": True, "duplicate": True,
                "draft_id": existing["draft_id"], "slot": existing["slot"],
                "status": existing["status"], "note": "该素材已有草稿，未重复创建",
            }, ensure_ascii=False)

        ctx["sequences"] += 1
        draft = {
            "draft_id": f"D{ctx['sequences']:04d}",
            "material_id": material_id,
            "title": mat.get("title"),
            "channel": mat.get("channel"),
            "format": mat.get("format"),
            "slot": slot,
            "status": status,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        ctx["draft_store"][material_id] = draft
        payload = {
            "material_id": material_id, "ok": True, "duplicate": False,
            "draft_id": draft["draft_id"], "slot": slot, "status": status,
            "note": "草稿已保存（不会自动发布）",
        }
        if status != requested_status:
            payload["status_adjusted"] = (f"请求 {requested_status} 与规则不符："
                                          f"该时段不可用/超营业时间/类型限制，已按待确认保存")
        return json.dumps(payload, ensure_ascii=False)
    return save_draft


# ---------------------------------------------------------------- 供 Agent 校验的参数规范
SCHEMAS = {
    "read_material": {"required": ["material_id"], "optional": []},
    "check_rules": {"required": ["material_id"], "optional": []},
    "query_slots": {"required": ["material_id"], "optional": []},
    "save_draft": {"required": ["material_id", "slot"], "optional": ["status"]},
}


def schema_error(action: str, args: dict) -> str | None:
    schema = SCHEMAS.get(action)
    if schema is None:
        return f"未知动作 {action}"
    if not isinstance(args, dict):
        return f"{action} 的参数必须是 JSON 对象"
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        return f"{action} 缺少参数：{', '.join(missing)}"
    extra = [k for k in args if k not in schema["required"] + schema["optional"]]
    if extra:
        return f"{action} 不接受参数：{', '.join(extra)}"
    mid = args.get("material_id")
    if not isinstance(mid, str) or not re.match(r"^[A-Z]\d{2}$", mid):
        return f"material_id 格式不正确：{mid!r}"
    if action == "save_draft" and hhmm(args.get("slot")) is None:
        return f"slot 必须是 HH:MM：{args.get('slot')!r}"
    return None


def build_registry(ctx: dict, today: date) -> dict:
    return {
        "read_material": build_read_material(ctx),
        "check_rules": build_check_rules(ctx),
        "query_slots": build_query_slots(ctx, today),
        "save_draft": build_save_draft(ctx),
    }
