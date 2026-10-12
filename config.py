import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    # 纯原生标准库兜底解析简易 .env 文件
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v

# 服务监听配置
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# DeepSeek 上游配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

# 日志输出目录（确保绝对路径并创建目录）
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs")).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 是否保存底层原始 JSON 报文（支持环境变量 DUMP_RAW_JSON 或启动参数 --raw）
import sys
DUMP_RAW_JSON = (
    os.getenv("DUMP_RAW_JSON", "false").lower() in ("true", "1", "yes") or
    "--raw" in sys.argv
)

# 预置的无效模板占位符列表
PLACEHOLDER_KEYS = {
    "your_deepseek_api_key_here",
    "sk-your-deepseek-api-key-here",
    "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
}

def is_valid_api_key(key: str | None) -> bool:
    """检查 API Key 是否为有效的非占位符密钥"""
    if not key:
        return False
    k = key.strip()
    if not k or k in PLACEHOLDER_KEYS or k.startswith("sk-your-"):
        return False
    return True

def get_effective_api_key(client_auth_header: str | None = None) -> str:
    """
    获取有效的 DeepSeek API Key。
    优先级策略：
    1. 优先使用本地 .env 或系统环境变量配置的 DEEPSEEK_API_KEY（非占位符）
    2. 若本地未配置，则从客户端请求头 Authorization: Bearer <token> 中提取
    """
    if is_valid_api_key(DEEPSEEK_API_KEY):
        return DEEPSEEK_API_KEY

    if client_auth_header:
        parts = client_auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
            if is_valid_api_key(token):
                return token
        elif len(parts) == 1:
            token = parts[0]
            if is_valid_api_key(token):
                return token

    return ""

