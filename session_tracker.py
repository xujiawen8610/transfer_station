import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple
from config import LOG_DIR

# Windows 保留设备名称列表
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

def sanitize_filename(name: str, max_len: int = 30) -> str:
    r"""
    清理字符串以生成合法的 Windows 文件夹/文件名。
    去除或替换非法的 Windows 字符: \ / : * ? " < > | 以及换行符等。
    同时防止 Windows 禁止的以点/空格结尾及系统保留设备名。
    """
    if not name:
        return "未命名任务"

    # 替换不可见换行和非法字符
    cleaned = re.sub(r'[\r\n\t]+', ' ', name)
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', cleaned).strip()
    # 压缩连续空格
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # Windows 路径不能以点、空格或下划线结尾
    cleaned = cleaned.rstrip('. _')
    if not cleaned:
        cleaned = "未命名任务"

    cleaned = cleaned[:max_len].rstrip('. _')
    if not cleaned:
        cleaned = "未命名任务"

    # 规避 Windows 保留设备名（如 CON, PRN, AUX, NUL 等）
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_task"

    return cleaned

def extract_user_messages(messages: List[Dict[str, Any]]) -> Tuple[int, str]:
    """
    从 messages 列表中提取所有 user 消息的总数以及最后一条 user 消息的文本内容。
    兼容处理 content 为字符串、None 或列表(多模态/内容块)的情况。
    """
    user_count = 0
    last_user_content = ""

    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_count += 1
            content = msg.get("content")
            if content is None:
                last_user_content = ""
            elif isinstance(content, str):
                last_user_content = content
            elif isinstance(content, list):
                # 如果是多模态结构（如 [{'type': 'text', 'text': '...'}]）
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text") or "")
                    elif isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and "text" in part:
                        text_parts.append(str(part.get("text") or ""))
                last_user_content = "".join(text_parts)
            else:
                last_user_content = str(content)

    return user_count, last_user_content.strip()

class SessionTracker:
    """
    基于第一性原理的会话跟踪器：
    不依赖复杂 Session ID，而是通过比对 messages 数组中最后一条 user 消息内容以及 user 消息数量：
    - 当 user 消息内容发生变化、user 消息数量变动、或上下文消息回缩（用户在客户端清空上下文重置会话）时，
      判定为新交互任务，新建专属文件夹并重置序号为 1。
    - 当处于同一个 user 提问的工具调用循环中时，复用该文件夹，序号依次递增 (01, 02, 03...)。
    """
    def __init__(self, base_log_dir: Path = LOG_DIR):
        self.base_log_dir = base_log_dir
        self.lock = asyncio.Lock()
        self.last_user_count: int = -1
        self.last_user_content: str = ""
        self.last_total_messages: int = 0
        self.current_folder: Path | None = None
        self.current_step: int = 0

    async def get_log_file_path(self, messages: List[Dict[str, Any]]) -> Path:
        """
        根据当前请求的 messages 计算应保存的日志文件路径。
        返回完整的 .txt 文件路径。
        """
        async with self.lock:
            user_count, current_user_content = extract_user_messages(messages)
            current_total_messages = len(messages)
            now = datetime.now()

            # 判断是否是新任务：
            # 1. 之前没有文件夹
            # 2. user 消息条数变动（用户追问了新问题）
            # 3. 最后一条 user 消息内容变动（用户开启了全新任务）
            # 4. 上下文总消息条数减少（用户在客户端清空了历史记录，即使提问相同也是新会话）
            is_new_session = (
                self.current_folder is None or
                user_count != self.last_user_count or
                current_user_content != self.last_user_content or
                current_total_messages < self.last_total_messages
            )

            if is_new_session:
                # 生成新任务文件夹：时间戳_摘要
                timestamp_str = now.strftime("%Y%m%d_%H%M%S")
                summary = sanitize_filename(current_user_content, max_len=25)
                base_folder_name = f"{timestamp_str}_{summary}"
                target_folder = self.base_log_dir / base_folder_name

                # 若同秒内已有同名文件夹（如快速测试或连续重置），追加微秒或递增序号避免冲突
                if target_folder.exists():
                    sub_sec = now.strftime("%f")[:3]
                    target_folder = self.base_log_dir / f"{base_folder_name}_{sub_sec}"
                    dup_idx = 1
                    while target_folder.exists():
                        target_folder = self.base_log_dir / f"{base_folder_name}_{sub_sec}_{dup_idx}"
                        dup_idx += 1

                self.current_folder = target_folder
                self.current_folder.mkdir(parents=True, exist_ok=True)

                self.current_step = 1
                self.last_user_count = user_count
                self.last_user_content = current_user_content
                self.last_total_messages = current_total_messages
            else:
                # 同一个任务中的后续步骤（工具调用、多步思考等）
                self.current_step += 1
                self.last_total_messages = current_total_messages

            # 生成文件名：带序号和时间，如 01_143025.txt
            time_tag = now.strftime("%H%M%S")
            filename = f"{self.current_step:02d}_{time_tag}.txt"
            return self.current_folder / filename


# 全局单例
session_tracker = SessionTracker()
