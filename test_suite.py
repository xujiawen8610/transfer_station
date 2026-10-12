import asyncio
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from session_tracker import SessionTracker, sanitize_filename, extract_user_messages
from log_formatter import format_request_section, format_response_section, build_full_log
from stream_handler import StreamAggregator, save_log_file

from config import is_valid_api_key, get_effective_api_key

def test_sanitize_filename():
    # 场景 1: Windows 特殊非法字符
    raw_title = '帮我做个爬虫: 检查 "URL" 是否存在? <test> | *demo* / \\ \n 换行'
    clean = sanitize_filename(raw_title, max_len=20)
    assert ":" not in clean
    assert "?" not in clean
    assert "<" not in clean
    assert ">" not in clean
    assert "|" not in clean
    assert "*" not in clean
    assert "/" not in clean
    assert "\\" not in clean
    assert "\n" not in clean
    assert len(clean) <= 20

    # 场景 2: 末尾禁止点与空格
    dot_title = "测试末尾有句点...."
    clean_dot = sanitize_filename(dot_title)
    assert not clean_dot.endswith(".")
    assert clean_dot == "测试末尾有句点"

    # 场景 3: Windows 保留设备名称（如 CON, AUX, NUL）
    con_title = "con"
    clean_con = sanitize_filename(con_title)
    assert clean_con == "con_task"

    # 场景 4: 全空或纯空格
    empty_title = "    "
    clean_empty = sanitize_filename(empty_title)
    assert clean_empty == "未命名任务"

    print("[PASS] test_sanitize_filename")

def test_config_api_key_validation():
    # 占位符必须被拒绝
    assert not is_valid_api_key("sk-your-deepseek-api-key-here")
    assert not is_valid_api_key("your_deepseek_api_key_here")
    assert not is_valid_api_key("")
    assert not is_valid_api_key(None)
    # 有效 Key 正常接受
    assert is_valid_api_key("sk-abcdef1234567890")
    print("[PASS] test_config_api_key_validation")

def test_extract_user_messages():
    # 场景 1: 普通字符串
    messages1 = [
        {"role": "system", "content": "You are a helper."},
        {"role": "user", "content": "你好，请写一个快速排序"},
        {"role": "assistant", "content": "没问题"}
    ]
    count1, last1 = extract_user_messages(messages1)
    assert count1 == 1
    assert last1 == "你好，请写一个快速排序"

    # 场景 2: 多模态列表格式
    messages2 = [
        {"role": "user", "content": [{"type": "text", "text": "分析这张图"}, {"type": "text", "text": "并总结"}]}
    ]
    count2, last2 = extract_user_messages(messages2)
    assert count2 == 1
    assert last2 == "分析这张图并总结"

    # 场景 3: 多个 user 提问
    messages3 = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"}
    ]
    count3, last3 = extract_user_messages(messages3)
    assert count3 == 2
    assert last3 == "第二问"

    # 场景 4: content 为 None 的边缘情况
    messages4 = [
        {"role": "user", "content": None}
    ]
    count4, last4 = extract_user_messages(messages4)
    assert count4 == 1
    assert last4 == ""
    print("[PASS] test_extract_user_messages")

async def test_session_tracker_workflow():
    tmp_dir = Path(tempfile.mkdtemp(prefix="agent_proxy_test_"))
    try:
        tracker = SessionTracker(base_log_dir=tmp_dir)

        # 1. 模拟第一次任务提问: "编写贪吃蛇"
        step1_messages = [
            {"role": "system", "content": "You are Cline."},
            {"role": "user", "content": "编写贪吃蛇游戏"}
        ]
        file1 = await tracker.get_log_file_path(step1_messages)
        folder1 = tracker.current_folder
        assert folder1 is not None
        assert folder1.exists()
        assert "编写贪吃蛇游戏" in folder1.name
        assert file1.name.startswith("01_")

        # 2. 模拟该任务下的工具调用循环: Agent 调用工具返回结果，user 消息未变
        step2_messages = [
            {"role": "system", "content": "You are Cline."},
            {"role": "user", "content": "编写贪吃蛇游戏"},
            {"role": "assistant", "content": "我先看一下目录", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "file.txt"}
        ]
        file2 = await tracker.get_log_file_path(step2_messages)
        assert tracker.current_folder == folder1  # 依然在同一个任务文件夹
        assert file2.name.startswith("02_")

        # 3. 模拟工具调用第 3 步
        step3_messages = step2_messages + [
            {"role": "assistant", "content": "我再写代码", "tool_calls": [{"id": "call_2", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_2", "content": "ok"}
        ]
        file3 = await tracker.get_log_file_path(step3_messages)
        assert tracker.current_folder == folder1
        assert file3.name.startswith("03_")

        # 4. 模拟用户清空上下文，重新提问相同问题（对话重置判定）
        reset_messages = [
            {"role": "system", "content": "You are Cline."},
            {"role": "user", "content": "编写贪吃蛇游戏"}
        ]
        file_reset = await tracker.get_log_file_path(reset_messages)
        folder_reset = tracker.current_folder
        assert folder_reset != folder1  # 消息列表长度骤降，正确识别为新会话
        assert file_reset.name.startswith("01_")

        # 5. 模拟用户发起全新任务: "帮我查一下明天天气"
        step5_messages = [
            {"role": "system", "content": "You are Cline."},
            {"role": "user", "content": "帮我查一下明天天气"}
        ]
        file5 = await tracker.get_log_file_path(step5_messages)
        folder5 = tracker.current_folder
        assert folder5 != folder_reset
        assert "帮我查一下明天天气" in folder5.name
        assert file5.name.startswith("01_")  # 序号重置为 01
        print("[PASS] test_session_tracker_workflow")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_stream_aggregator():
    agg = StreamAggregator()

    # 模拟 DeepSeek-R1 的流式 chunk 序列 (带思考链、文本内容、以及流式分片 tool_call)
    chunks = [
        # chunk 1: 思考链开始
        'data: {"id":"chatcmpl-123","model":"deepseek-reasoner","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"用户想要"},"finish_reason":null}]}',
        # chunk 2: 思考链追加
        'data: {"choices":[{"index":0,"delta":{"reasoning_content":"写一个文件"},"finish_reason":null}]}',
        # chunk 3: 思考链结束，开始回复文本
        'data: {"choices":[{"index":0,"delta":{"content":"好的，正在为你创建"},"finish_reason":null}]}',
        # chunk 4: 文本追加
        'data: {"choices":[{"index":0,"delta":{"content":"文件。"},"finish_reason":null}]}',
        # chunk 5: 工具调用 id 与 name
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"write_file","arguments":""}}]},"finish_reason":null}]}',
        # chunk 6: 工具调用 arguments 分片 1
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\": "}}]},"finish_reason":null}]}',
        # chunk 7: 工具调用 arguments 分片 2
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"test.py\\"}"}}]},"finish_reason":null}]}',
        # chunk 8: 结束与 usage
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":50,"completion_tokens":30,"total_tokens":80,"prompt_cache_hit_tokens":40,"prompt_cache_miss_tokens":10}}',
        'data: [DONE]'
    ]

    for c in chunks:
        agg.feed_chunk(c)

    res = agg.build_full_response()
    choice = res["choices"][0]
    msg = choice["message"]

    assert msg["reasoning_content"] == "用户想要写一个文件"
    assert msg["content"] == "好的，正在为你创建文件。"
    assert choice["finish_reason"] == "tool_calls"
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_abc"
    assert tc["function"]["name"] == "write_file"
    assert tc["function"]["arguments"] == '{"path": "test.py"}'
    assert res["usage"]["total_tokens"] == 80
    assert res["usage"]["prompt_cache_hit_tokens"] == 40
    print("[PASS] test_stream_aggregator")

def test_stream_aggregator_error():
    agg = StreamAggregator()
    error_chunk = 'data: {"error":{"message":"The engine is overloaded","type":"overloaded_error","code":"engine_overloaded"}}'
    agg.feed_chunk(error_chunk)
    res = agg.build_full_response()
    assert "error" in res
    assert res["error"]["message"] == "The engine is overloaded"
    print("[PASS] test_stream_aggregator_error")

def test_log_formatter():
    req_data = {
        "model": "deepseek-reasoner",
        "stream": True,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "请写一个 hello world"},
            {
                "role": "assistant",
                "content": "正在准备",
                "reasoning_content": "先查看目录",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run", "arguments": "{\"cmd\": \"echo hi\"}"}
                }]
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "hi"}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "run",
                    "description": "执行终端命令"
                }
            }
        ]
    }

    resp_data = {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "print('Hello World')",
                "reasoning_content": "分析：直接使用 Python 内置 print 函数即可",
                "tool_calls": []
            }
        }],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "total_tokens": 165,
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 20
        }
    }

    log = build_full_log(
        request_data=req_data,
        req_time=datetime.now(),
        response_data=resp_data,
        resp_time=datetime.now(),
        duration_s=1.23,
        status_code=200
    )

    assert "========================= Agent -> LLM =========================" in log
    assert "[system]" in log
    assert "You are a coding assistant." in log
    assert "[user]" in log
    assert "请写一个 hello world" in log
    assert "[assistant]" in log
    assert "[call_tool: run]" in log
    assert "[tool]" in log
    assert "========================= LLM -> Agent =========================" in log
    assert "[reasoning]" in log
    assert "分析：直接使用 Python 内置 print 函数即可" in log
    assert "print('Hello World')" in log
    # 验证不包含多余冗余信息
    assert "Total Tokens" not in log
    assert "Available Tools" not in log

    # 测试错误响应排版
    err_resp_data = {
        "error": {
            "type": "authentication_error",
            "code": "invalid_api_key",
            "message": "Invalid API key provided."
        }
    }
    err_log = build_full_log(
        request_data=req_data,
        req_time=datetime.now(),
        response_data=err_resp_data,
        resp_time=datetime.now(),
        duration_s=0.5,
        status_code=401
    )
    assert "[error]" in err_log
    assert "invalid_api_key" in err_log
    assert "Invalid API key provided." in err_log
    print("[PASS] test_log_formatter")

def test_raw_json_mode():
    import config
    orig = config.DUMP_RAW_JSON
    try:
        config.DUMP_RAW_JSON = True
        req_data = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        resp_data = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
        log = build_full_log(
            request_data=req_data,
            req_time=datetime.now(),
            response_data=resp_data,
            resp_time=datetime.now(),
            duration_s=1.0,
            status_code=200
        )
        assert "========================= Agent -> LLM =========================\n" in log
        assert "========================= LLM -> Agent =========================\n" in log
        assert '"model": "test"' in log
        assert '"content": "hello"' in log
        assert "[user]" not in log
        print("[PASS] test_raw_json_mode")
    finally:
        config.DUMP_RAW_JSON = orig

async def main():
    test_sanitize_filename()
    test_config_api_key_validation()
    test_extract_user_messages()
    await test_session_tracker_workflow()
    test_stream_aggregator()
    test_stream_aggregator_error()
    test_log_formatter()
    test_raw_json_mode()
    print("\n[SUCCESS] 所有测试用例全部顺利通过！")

if __name__ == "__main__":
    asyncio.run(main())

