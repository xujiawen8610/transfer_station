# OpenAI-DeepSeek Agent Proxy (反向代理中转站)

专为**研究 Agent 底层交互原理、系统提示词（System Prompt）以及工具调用（Tool Calling）循环**而设计的 OpenAI API 兼容反向代理中转服务。

后端通过 Python (FastAPI + httpx + uvicorn) 构建，前端完全兼容各种主流 VS Code Agent 插件（如 **Cline**、**Roo Code**、**Continue** 等），后端连接 **DeepSeek 官方 API**（支持 `deepseek-chat` 和带思考链的 `deepseek-reasoner` / R1）。

---

## ✨ 核心特性

1. **第一性原理任务识别与自动归类**：
   - 无需依赖客户端传递复杂的 Session ID。
   - 自动检测请求体 `messages` 数组中 `role: 'user'` 消息的数量与最后一条内容。
   - 一旦用户输入了新提问或开启新任务，服务自动新建以“时间戳 + 用户提问摘要”命名的专属交互文件夹。
   - 同一次用户提问下的多步工具调用（Tool Call 循环），自动追加归档至同一文件夹，通信步骤序号递增（`01_HHMMSS.txt`、`02_HHMMSS.txt`...）。

2. **流式零延迟透传 + 内存聚合完整落盘**：
   - VS Code 插件普遍采用 `stream=true`（SSE 事件流）。
   - 本代理采用边收取边 `yield` 的流式管道，**实时透传给客户端，保证打字机效果流畅丝滑、绝不卡顿**。
   - 内存流式聚合器同时累积并拼装文本增量、**DeepSeek-R1 思考链（`reasoning_content`）** 以及分片传输的 **`tool_calls` 参数**，在流结束时完整排版落盘。

3. **忠实记录全量数据，清晰排版**：
   - 日志文件分为明确的两大部分：
     1. `[REQUEST] Agent -> LLM`：模型、超参数、可用工具列表，以及逐条解构的上下文（System Prompt、User 提问、Assistant 历史、Tool 返回值）。
     2. `[RESPONSE] LLM -> Agent`：状态码、响应耗时、**思考链 Reasoning**、文本回复 Content、**工具调用 Tool Calls（格式化 JSON 参数）** 以及 Token 统计。

4. **KISS 原则，轻量优雅开箱即用**：
   - 代码结构扁平，模块职责单一，无繁琐冗余设计。

---

## 📁 目录结构

```text
中转/
├── .env.example          # 环境变量示例文件
├── .gitignore            # Git 忽略配置
├── requirements.txt      # Python 依赖清单
├── config.py             # 配置读取与 API Key 智能解析
├── session_tracker.py    # 第一性原理会话识别与目录管理器
├── log_formatter.py      # 日志排版格式化工具
├── stream_handler.py     # 流式 SSE 实时透传与聚合落盘核心
├── main.py               # FastAPI 服务主入口与路由
├── test_suite.py         # 全流程单元与模拟测试脚本
└── logs/                 # 自动生成的交互日志归档目录（运行后生成）
    └── 20261012_170000_编写贪吃蛇游戏/
        ├── 01_170001.txt # 第一次交互：Agent 规划并调用查看目录工具
        ├── 02_170010.txt # 第二次交互：Agent 读取文件后编写代码
        └── 03_170025.txt # 第三次交互：Agent 完成任务并回复用户
```

---

## 🚀 快速启动

### 1. 安装依赖

确保本机已安装 Python 3.10+，在当前目录下打开终端运行：

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
# Windows PowerShell:
Copy-Item .env.example .env

# 或 CMD:
copy .env.example .env
```

打开 `.env` 文件，填入你的 DeepSeek API Key：

```env
# 必填：你的 DeepSeek API Key（https://platform.deepseek.com 获取）
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 默认基础地址（可保持默认）
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 代理监听配置
HOST=127.0.0.1
PORT=8000
LOG_DIR=./logs
```

> **提示**：如果未在 `.env` 中配置 Key，代理服务也会自动尝试读取客户端请求头传入的 `Authorization: Bearer <key>`，极具灵活性。

### 3. 启动中转代理服务

在项目根目录下运行（支持两种日志模式）：

* **模式 A：易读纯文本对话模式（默认）**
  ```bash
  python main.py
  ```

* **模式 B：原始底层完整 JSON 报文模式（Raw JSON）**
  ```bash
  python main.py --raw
  ```
  *(也可在 `.env` 中设置 `DUMP_RAW_JSON=true` 实现同样效果)*

服务启动后，终端会打印当前日志模式，在浏览器中访问 `http://127.0.0.1:8000` 即可查看服务运行状态。

---

## ⚙️ VS Code 客户端配置指南

以常用 Agent 插件为例：

### 1. Cline (Claude Dev)
1. 点击 VS Code 左侧 Cline 图标打开插件面板。
2. 点击右上角 ⚙️ **设置 (Settings)**。
3. **API Provider** 选择：`OpenAI Compatible`。
4. **Base URL** 填写：`http://127.0.0.1:8000/v1`
5. **API Key** 填写：任意字符（如 `sk-proxy`，若 `.env` 未配置则填写真实 DeepSeek Key）。
6. **Model ID** 填写：`deepseek-chat` 或 `deepseek-reasoner`。
7. 保存后即可开始对话！

### 2. Roo Code (Roo Clinic)
1. 打开 Roo Code 设置面板。
2. Provider 选择 `OpenAI-Compatible`。
3. Base URL 填写 `http://127.0.0.1:8000/v1`。
4. Model ID 填写 `deepseek-chat`。

### 3. Continue
在 `~/.continue/config.json` 中配置：
```json
{
  "models": [
    {
      "title": "DeepSeek (Local Proxy)",
      "provider": "openai",
      "model": "deepseek-chat",
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "sk-proxy"
    }
  ]
}
```

---

### 📄 日志文件格式示例

每次通信保存的 `.txt` 文件示例排版如下（极简、纯粹、零多余废话）：

```text
========================= Agent -> LLM =========================

[system]
You are Cline, a highly skilled software engineer...

[user]
请帮我写一个快速排序算法并测试

[assistant]
好的，我先检查一下当前目录的文件。
[call_tool: list_dir]
{
  "path": "."
}

[tool] (tool_call_id: call_001)
README.md main.py

========================= LLM -> Agent =========================

[reasoning]
用户需要一个快速排序算法，当前目录下没有现成代码文件，我应该新建一个 quick_sort.py 文件来编写代码。

[call_tool: write_to_file]
{
  "path": "quick_sort.py",
  "content": "def quick_sort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quick_sort(left) + middle + quick_sort(right)"
}

我将为你创建一个 quick_sort.py 文件并编写快速排序函数。
```

---

## 🧪 验证与测试

项目自带完整的单元测试套件：

```bash
python test_suite.py
```

测试覆盖：
- 路径安全与 Windows 保留设备名（CON, PRN, AUX 等）及末尾非法点过滤
- `user` 提问提取、`None` 类型判空与多模态结构兼容
- 工具循环（`assistant` + `tool`）复用同一会话目录与步骤自增
- 用户新提问及客户端清空会话（消息回缩）触发新文件夹生成与序号重置
- DeepSeek-R1 流式 SSE 思考链增量、文本增量及分片 `tool_calls` 聚合还原
- 流中错误 chunk 捕获与上游网络故障全链路错误日志排版落盘
- 请求与响应全量排版格式化、历史思考链保留及 DeepSeek 缓存命中统计
- API Key 模板占位符校验与客户端 Bearer Token 优先兜底逻辑

