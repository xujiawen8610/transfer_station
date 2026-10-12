import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List
from log_formatter import build_full_log

class StreamAggregator:
    """
    流式响应聚合器：
    实时透传 SSE 数据的同时，在内存中还原完整的回复、思考链（DeepSeek-R1）、工具调用及元信息。
    """
    def __init__(self):
        self.response_id: str = ""
        self.model: str = ""
        self.finish_reason: str = "stop"
        self.full_content: str = ""
        self.full_reasoning: str = ""
        # 针对流式 tool_calls 按 index 累加
        self.tool_calls_map: Dict[int, Dict[str, Any]] = {}
        self.usage: Dict[str, Any] = {}
        self.error: Dict[str, Any] = {}

    def feed_chunk(self, line: str):
        """解析一条 SSE data 行并累积数据"""
        line_clean = line.strip()
        if not line_clean.startswith("data:"):
            return
        data_str = line_clean[len("data:"):].strip()
        if data_str == "[DONE]":
            return

        try:
            chunk = json.loads(data_str)
        except Exception:
            return

        # 捕获流中可能返回的 error 字段
        if "error" in chunk and chunk["error"]:
            self.error = chunk["error"]

        if not self.response_id and "id" in chunk:
            self.response_id = chunk["id"]
        if not self.model and "model" in chunk:
            self.model = chunk["model"]
        if "usage" in chunk and chunk["usage"]:
            self.usage = chunk["usage"]

        choices = chunk.get("choices", [])
        if not choices:
            return

        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]

        delta = choice.get("delta", {})

        # 1. 累积文本回复
        content_delta = delta.get("content")
        if content_delta:
            self.full_content += content_delta

        # 2. 累积思考链 (DeepSeek-R1 / reasoner)
        reasoning_delta = delta.get("reasoning_content")
        if reasoning_delta:
            self.full_reasoning += reasoning_delta

        # 3. 累积工具调用 (Tool Calls)
        tool_deltas = delta.get("tool_calls") or []
        if tool_deltas:
            for tc in tool_deltas:
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index") if tc.get("index") is not None else 0
                fn = tc.get("function") or {}

                if idx not in self.tool_calls_map:
                    self.tool_calls_map[idx] = {
                        "id": tc.get("id") or "",
                        "type": tc.get("type") or "function",
                        "function": {
                            "name": fn.get("name") or "",
                            "arguments": fn.get("arguments") or ""
                        }
                    }
                else:
                    existing = self.tool_calls_map[idx]
                    if tc.get("id"):
                        existing["id"] = tc.get("id")
                    if fn.get("name"):
                        existing["function"]["name"] += str(fn.get("name"))
                    if fn.get("arguments"):
                        existing["function"]["arguments"] += str(fn.get("arguments"))

    def build_full_response(self) -> Dict[str, Any]:
        """构建聚合后的标准响应字典"""
        # 将 tool_calls_map 转换为按 index 排序的列表
        tool_calls_list = []
        for idx in sorted(self.tool_calls_map.keys()):
            tool_calls_list.append(self.tool_calls_map[idx])

        resp = {
            "id": self.response_id,
            "model": self.model,
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": self.full_content,
                        "reasoning_content": self.full_reasoning,
                        "tool_calls": tool_calls_list
                    }
                }
            ],
            "usage": self.usage
        }
        if self.error:
            resp["error"] = self.error
        return resp


async def save_log_file(file_path: Path, log_content: str):
    """异步将日志文本写入文件"""
    def _write():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(log_content)
    await asyncio.to_thread(_write)

async def handle_stream_response(
    httpx_response,
    request_data: Dict[str, Any],
    req_time: datetime,
    log_file_path: Path
) -> AsyncGenerator[bytes, None]:
    """
    实时流式透传生成器：
    边收取上游字节边 yield 给客户端（保持打字机效果零等待），
    同时累积内容并在流结束后将完整记录落盘。
    """
    aggregator = StreamAggregator()
    status_code = httpx_response.status_code

    try:
        async for line in httpx_response.aiter_lines():
            # 累积解析
            aggregator.feed_chunk(line)
            # 实时透传当前行（1:1 保持 SSE 换行格式）
            yield f"{line}\n".encode("utf-8")
    finally:
        # 关闭上游连接
        await httpx_response.aclose()
        # 流传输结束（无论是正常完成还是被客户端打断），进行落盘
        resp_time = datetime.now()
        duration_s = (resp_time - req_time).total_seconds()
        full_resp = aggregator.build_full_response()
        log_text = build_full_log(
            request_data=request_data,
            req_time=req_time,
            response_data=full_resp,
            resp_time=resp_time,
            duration_s=duration_s,
            status_code=status_code
        )
        try:
            await save_log_file(log_file_path, log_text)
            print(f"[OK] 交互日志已保存至: {log_file_path}")
        except Exception as e:
            print(f"[ERROR] 保存日志文件失败: {e}")
