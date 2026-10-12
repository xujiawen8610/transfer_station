import json
import uvicorn
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, Response, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

import config
from session_tracker import session_tracker
from stream_handler import handle_stream_response, save_log_file
from log_formatter import build_full_log

@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理全局异步 HTTP 客户端生命周期"""
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)
    )
    print("=" * 60)
    print(f"[*] DeepSeek 代理中转服务已就绪")
    print(f"[*] 监听地址: http://{config.HOST}:{config.PORT}")
    print(f"[*] 上游地址: {config.DEEPSEEK_BASE_URL}")
    print(f"[*] 日志目录: {config.LOG_DIR}")
    mode_desc = "原始 JSON 报文模式 (Raw JSON)" if config.DUMP_RAW_JSON else "纯文本对话模式"
    print(f"[*] 日志模式: {mode_desc}")
    if config.DEEPSEEK_API_KEY and config.DEEPSEEK_API_KEY != "your_deepseek_api_key_here":
        print(f"[*] API Key : 已从本地配置加载 (sk-***{config.DEEPSEEK_API_KEY[-4:] if len(config.DEEPSEEK_API_KEY) > 4 else ''})")
    else:
        print("[!] 警告: 本地未检测到 DEEPSEEK_API_KEY，将尝试透传客户端请求头中的 API Key。")
    print("=" * 60)
    yield
    await app.state.client.aclose()
    print("[*] HTTP 客户端已优雅关闭")

app = FastAPI(
    title="OpenAI-DeepSeek Agent Proxy",
    description="面向 Agent 底层研究的 OpenAI 协议兼容反向代理中转站",
    version="1.0.0",
    lifespan=lifespan
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
@app.get("/health")
async def health_check():
    """中转站运行状态与说明页"""
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Agent Proxy - 运行中</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 40px; background: #0f172a; color: #f8fafc; line-height: 1.6; }}
            .container {{ max-width: 800px; margin: 0 auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
            h1 {{ color: #38bdf8; margin-top: 0; }}
            .badge {{ display: inline-block; padding: 4px 12px; background: #22c55e; color: #fff; border-radius: 20px; font-size: 14px; font-weight: bold; }}
            pre {{ background: #0f172a; padding: 15px; border-radius: 8px; overflow-x: auto; color: #a5f3fc; }}
            code {{ font-family: Consolas, Monaco, monospace; }}
            ul {{ padding-left: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Agent Proxy 中转站 <span class="badge">RUNNING</span></h1>
            <p>基于第一性原理打造的 OpenAI 协议兼容反向代理服务，支持自动将 Agent 与 LLM 的每次交互完整解析并排版落盘。</p>
            
            <h3>配置参数：</h3>
            <ul>
                <li><strong>监听地址：</strong> <code>http://{config.HOST}:{config.PORT}</code></li>
                <li><strong>上游接口：</strong> <code>{config.DEEPSEEK_BASE_URL}</code></li>
                <li><strong>持久化日志路径：</strong> <code>{config.LOG_DIR}</code></li>
            </ul>

            <h3>VS Code 客户端配置（Cline / Roo Code / Continue）：</h3>
            <pre>
API Provider : OpenAI Compatible
Base URL     : http://{config.HOST}:{config.PORT}/v1
API Key      : (任意字符串，或你的 DeepSeek API Key)
Model ID     : deepseek-chat 或 deepseek-reasoner
            </pre>
        </div>
    </body>
    </html>
    """)

@app.get("/models")
@app.get("/v1/models")
async def list_models(request: Request):
    """
    模型列表端点。
    VS Code 插件（如 Cline、Roo Code）连接时通常会请求此接口验证连通性。
    优先向上游转发拉取；若失败或未配 key 则返回常用 DeepSeek 模型列表。
    """
    api_key = config.get_effective_api_key(request.headers.get("Authorization"))
    client: httpx.AsyncClient = request.app.state.client

    if api_key:
        try:
            target_url = f"{config.DEEPSEEK_BASE_URL}/models"
            resp = await client.get(target_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

    # 兜底返回常用模型
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"}
        ]
    }

@app.get("/models/{model_id}")
@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    """单模型信息查询端点，防止客户端探活报错"""
    return {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "deepseek"
    }

@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    核心端点：代理转发 chat/completions 请求
    - 支持流式 (stream=true, SSE) 与非流式 (stream=false)
    - 自动依据 messages 第一性原理归类用户交互任务并保存详细交互 .txt 日志
    """
    req_time = datetime.now()

    # 1. 解析请求 JSON
    try:
        request_data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无效的 JSON 请求体: {str(e)}")

    messages = request_data.get("messages", [])
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="请求体必须包含 messages 数组")

    # 兼容处理现代 OpenAI SDK 的 max_completion_tokens
    if "max_completion_tokens" in request_data:
        if "max_tokens" not in request_data:
            request_data["max_tokens"] = request_data["max_completion_tokens"]
        del request_data["max_completion_tokens"]

    # 2. 确定 API Key
    client_auth = request.headers.get("Authorization")
    api_key = config.get_effective_api_key(client_auth)
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="缺少 DeepSeek API Key！请在本地 .env 文件中设置 DEEPSEEK_API_KEY，或者在客户端请求头中传入 Authorization: Bearer <key>"
        )

    # 3. 第一性原理确定日志保存路径
    log_file_path = await session_tracker.get_log_file_path(messages)
    stream = request_data.get("stream", False)
    model = request_data.get("model", "unknown")

    print(f"\n[->] 收到请求: 模型={model} | 流式={stream} | 上下文消息数={len(messages)}")
    print(f"     日志文件将保存至: {log_file_path}")

    # 4. 组装上游请求
    target_url = f"{config.DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json"
    }

    client: httpx.AsyncClient = request.app.state.client

    # 5. 分支处理：流式与非流式
    if stream:
        try:
            req = client.build_request("POST", target_url, headers=headers, json=request_data)
            upstream_resp = await client.send(req, stream=True)
        except Exception as e:
            err_msg = f"连接上游 DeepSeek API 失败: {str(e)}"
            print(f"[!] 错误: {err_msg}")
            # 记录异常连接日志，方便开发者排查
            resp_time = datetime.now()
            duration_s = (resp_time - req_time).total_seconds()
            err_json = {"error": {"message": err_msg, "type": "connect_error", "code": "upstream_unavailable"}}
            log_text = build_full_log(
                request_data=request_data,
                req_time=req_time,
                response_data=err_json,
                resp_time=resp_time,
                duration_s=duration_s,
                status_code=502
            )
            try:
                await save_log_file(log_file_path, log_text)
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=err_msg)

        # 如果上游返回了非 200 错误状态码（如 400/401/429 等）
        if upstream_resp.status_code != 200:
            error_content = await upstream_resp.aread()
            await upstream_resp.aclose()
            # 记录错误日志
            resp_time = datetime.now()
            duration_s = (resp_time - req_time).total_seconds()
            try:
                err_json = json.loads(error_content.decode("utf-8"))
            except Exception:
                err_json = {"error": error_content.decode("utf-8", errors="ignore")}
            log_text = build_full_log(
                request_data=request_data,
                req_time=req_time,
                response_data=err_json,
                resp_time=resp_time,
                duration_s=duration_s,
                status_code=upstream_resp.status_code
            )
            try:
                await save_log_file(log_file_path, log_text)
            except Exception as e:
                print(f"[ERROR] 保存日志文件失败: {e}")
            return Response(
                content=error_content,
                status_code=upstream_resp.status_code,
                media_type="application/json"
            )

        # 正常流式透传（增加防缓冲响应头，保证打字机流畅性）
        return StreamingResponse(
            handle_stream_response(
                httpx_response=upstream_resp,
                request_data=request_data,
                req_time=req_time,
                log_file_path=log_file_path
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        # 非流式请求
        try:
            resp = await client.post(target_url, headers=headers, json=request_data)
        except Exception as e:
            err_msg = f"请求上游 DeepSeek API 失败: {str(e)}"
            print(f"[!] 错误: {err_msg}")
            resp_time = datetime.now()
            duration_s = (resp_time - req_time).total_seconds()
            err_json = {"error": {"message": err_msg, "type": "connect_error", "code": "upstream_unavailable"}}
            log_text = build_full_log(
                request_data=request_data,
                req_time=req_time,
                response_data=err_json,
                resp_time=resp_time,
                duration_s=duration_s,
                status_code=502
            )
            try:
                await save_log_file(log_file_path, log_text)
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=err_msg)

        resp_time = datetime.now()
        duration_s = (resp_time - req_time).total_seconds()

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_content": resp.text}

        # 格式化并落盘保存
        log_text = build_full_log(
            request_data=request_data,
            req_time=req_time,
            response_data=resp_json,
            resp_time=resp_time,
            duration_s=duration_s,
            status_code=resp.status_code
        )
        try:
            await save_log_file(log_file_path, log_text)
            print(f"[OK] 交互日志已保存至: {log_file_path}")
        except Exception as e:
            print(f"[ERROR] 保存日志文件失败: {e}")

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json")
        )

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
