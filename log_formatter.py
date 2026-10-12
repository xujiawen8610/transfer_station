import json
from datetime import datetime
from typing import Dict, Any
import config

def pretty_json(data: Any) -> str:
    """美化 JSON 字符串或数据结构"""
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return data
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return str(data)

def format_request_section(request_data: Dict[str, Any]) -> str:
    """
    格式化 Agent 发给 LLM 的请求内容：
    纯粹记录各角色的真实对话与工具交互，不输出任何多余的统计或工具列表。
    """
    lines = ["========================= Agent -> LLM ========================="]
    messages = request_data.get("messages", [])

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "unknown"

        # 角色标头
        if role == "tool":
            tool_id = msg.get("tool_call_id", "")
            lines.append(f"\n[{role}] (tool_call_id: {tool_id})" if tool_id else f"\n[{role}]")
        else:
            lines.append(f"\n[{role}]")

        # 思考链 (若历史消息中存在)
        reasoning = msg.get("reasoning_content")
        if reasoning:
            lines.append(f"[reasoning]\n{str(reasoning).strip()}")

        # 正文内容
        content = msg.get("content")
        if content is not None:
            if isinstance(content, str):
                lines.append(content)
            else:
                lines.append(pretty_json(content))

        # 历史工具调用
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = fn.get("arguments") or ""
                lines.append(f"[call_tool: {name}]\n{pretty_json(args)}")

    return "\n".join(lines)

def format_response_section(response_data: Dict[str, Any]) -> str:
    """
    格式化 LLM 给 Agent 返回的响应内容：
    仅记录思考链、工具调用与文本回复本身。
    """
    lines = ["\n========================= LLM -> Agent ========================="]

    # 错误信息（仅在出错时呈现）
    if "error" in response_data:
        err = response_data["error"]
        lines.append(f"\n[error]\n{pretty_json(err)}")
        return "\n".join(lines)

    if "raw_content" in response_data:
        lines.append(f"\n{str(response_data['raw_content']).strip()}")
        return "\n".join(lines)

    choices = response_data.get("choices") or []
    if choices:
        choice = choices[0]
        message = choice.get("message") or choice.get("delta") or {}

        # 1. 思考链 (DeepSeek-R1 / reasoner)
        reasoning = message.get("reasoning_content")
        if reasoning:
            lines.append(f"\n[reasoning]\n{str(reasoning).strip()}")

        # 2. 工具调用 (Tool Calls)
        tool_calls = message.get("tool_calls") or []
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = fn.get("arguments") or ""
                lines.append(f"\n[call_tool: {name}]\n{pretty_json(args)}")

        # 3. 最终文本回复
        content = message.get("content")
        if content:
            lines.append(f"\n{str(content).strip()}")

    return "\n".join(lines)

def build_full_log(
    request_data: Dict[str, Any],
    req_time: datetime,
    response_data: Dict[str, Any],
    resp_time: datetime,
    duration_s: float,
    status_code: int = 200
) -> str:
    """
    组合完整的通信日志文本
    - 当开启 DUMP_RAW_JSON 时：保持相同的分割线，内部保存真实的底层大 JSON
    - 默认时：保存纯文本对话提取内容
    """
    if config.DUMP_RAW_JSON:
        req_json_str = pretty_json(request_data)
        resp_json_str = pretty_json(response_data)
        return (
            "========================= Agent -> LLM =========================\n"
            f"{req_json_str}\n\n"
            "========================= LLM -> Agent =========================\n"
            f"{resp_json_str}\n"
        )

    req_text = format_request_section(request_data)
    resp_text = format_response_section(response_data)
    return f"{req_text}\n{resp_text}\n"

