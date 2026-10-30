"""测试辅助：构建请求上下文与可控脚本模型。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cli import load_requests, make_request_payload  # noqa: E402


def get_request(request_id: str):
    for r in load_requests():
        if r["id"] == request_id:
            return r
    raise KeyError(request_id)


def payload_and_ctx(request_id: str):
    return make_request_payload(get_request(request_id))


class ScriptLLM:
    """按脚本返回固定决策的假模型（用于验证系统对模型越权/虚报/非法输出的防御）。"""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def complete(self, system, user):
        if not self.decisions:
            return json.dumps({"thought": "end", "action": "finish", "arguments": {"summary": ""}})
        d = self.decisions.pop(0)
        return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
