"""
即梦 API OpenAI 兼容接口封装
将即梦的图片生成API封装为OpenAI Chat Completions兼容格式
"""

import os
import re
import json
import time
import uuid
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Union

import httpx
from fastapi import FastAPI, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich.console import Console
from dotenv import load_dotenv

load_dotenv()

# 配置
JIMENG_BASE_URL = os.getenv("JIMENG_BASE_URL", "http://localhost:5100")
SERVER_PORT = int(os.getenv("SERVER_PORT", "18765"))
BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
DASHBOARD_FILE = os.path.join(STATIC_DIR, "index.html")
DATA_FILE = os.getenv("DATA_FILE", os.path.join(BASE_DIR, "data.json"))

# Rich console 用于美化输出
console = Console()

app = FastAPI(
    title="即梦 OpenAI 兼容接口",
    description="将即梦图片生成API封装为OpenAI Chat Completions兼容格式",
    version="1.0.0"
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

ws_manager = ConnectionManager()

# 调用记录统计
call_stats = {
    "total_calls": 0,
    "successful_calls": 0,
    "failed_calls": 0,
    "calls_history": []
}

# Session池管理
session_pool = {
    "sessions": {},  # {session_id: {email, password, fail_count, enabled, last_used, created_at}}
    "settings": {
        "max_fail_count": int(os.getenv("MAX_FAIL_COUNT", "3")),
        "jimeng_base_url": JIMENG_BASE_URL,
        "session_prefix": os.getenv("SESSION_PREFIX", "hk-"),
        "auto_enable_at_midnight": True  # 是否在次日凌晨 00:00 自动解禁
    }
}

DEFAULT_LOCAL_JIMENG_URL = "http://localhost:5100"


def normalize_session_record(session_id: str, info: Optional[dict]) -> dict:
    """补齐旧数据缺失字段，统一 session 结构。"""
    info = info or {}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "email": info.get("email", "unknown"),
        "password": info.get("password", ""),
        "fail_count": info.get("fail_count", 0),
        "enabled": info.get("enabled", True),
        "last_used": info.get("last_used"),
        "created_at": info.get("created_at", now_str),
        "disabled_at": info.get("disabled_at"),
        "auto_enable_at": info.get("auto_enable_at"),
    }


def save_data():
    """保存数据到文件"""
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(session_pool, f, ensure_ascii=False, indent=2)
    except Exception as e:
        console.print(f"[red]⚠️ 保存数据失败: {e}[/red]")


def load_data():
    """从文件加载数据"""
    global session_pool
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # 合并加载的数据
                if "sessions" in loaded:
                    session_pool["sessions"] = {
                        sid: normalize_session_record(sid, info)
                        for sid, info in loaded["sessions"].items()
                    }
                if "settings" in loaded:
                    session_pool["settings"].update(loaded["settings"])
                    persisted_url = session_pool["settings"].get("jimeng_base_url")
                    # 兼容旧 data.json 中写死 localhost 的情况，避免 Docker 新部署时
                    # 被历史配置覆盖回容器内不可达的地址。
                    if persisted_url == DEFAULT_LOCAL_JIMENG_URL and JIMENG_BASE_URL != DEFAULT_LOCAL_JIMENG_URL:
                        session_pool["settings"]["jimeng_base_url"] = JIMENG_BASE_URL
                console.print(f"[green]✅ 已加载 {len(session_pool['sessions'])} 个Session[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠️ 加载数据失败: {e}[/yellow]")


# ============== Session管理函数 ==============

def parse_session_line(line: str) -> Optional[dict]:
    """
    解析session行
    格式: email=xxx----password=xxx----sessionid=xxx;
    """
    line = line.strip()
    if not line:
        return None
    
    # 提取各字段
    email_match = re.search(r'email=([^-]+?)----', line)
    password_match = re.search(r'password=([^-]+?)----', line)
    sessionid_match = re.search(r'sessionid=([^;]+)', line)
    
    if not sessionid_match:
        return None
    
    return {
        "email": email_match.group(1) if email_match else "unknown",
        "password": password_match.group(1) if password_match else "",
        "session_id": sessionid_match.group(1).strip()
    }


def import_sessions(content: str) -> dict:
    """导入sessions，返回导入结果统计"""
    lines = content.strip().split('\n')
    imported = 0
    skipped = 0
    
    for line in lines:
        parsed = parse_session_line(line)
        if parsed and parsed["session_id"]:
            sid = parsed["session_id"]
            if sid not in session_pool["sessions"]:
                session_pool["sessions"][sid] = normalize_session_record(sid, {
                    "email": parsed["email"],
                    "password": parsed["password"],
                    "fail_count": 0,
                    "enabled": True,
                    "last_used": None,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "disabled_at": None,
                    "auto_enable_at": None
                })
                imported += 1
            else:
                skipped += 1
    
    # 保存到文件
    if imported > 0:
        save_data()
    
    return {"imported": imported, "skipped": skipped, "total": len(session_pool["sessions"])}


def get_random_session() -> Optional[tuple[str, dict]]:
    """获取一个随机可用的session"""
    available = [
        (sid, info) for sid, info in session_pool["sessions"].items()
        if info["enabled"] and info["fail_count"] < session_pool["settings"]["max_fail_count"]
    ]
    
    if not available:
        return None
    
    return random.choice(available)


async def mark_session_failed(session_id: str, disable_until_midnight: bool = False):
    """标记session失败，增加失败计数"""
    if session_id in session_pool["sessions"]:
        session = session_pool["sessions"][session_id]
        session["fail_count"] += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        auto_enable = session_pool["settings"].get("auto_enable_at_midnight", True)
        
        if disable_until_midnight:
            session["enabled"] = False
            session["disabled_at"] = now_str
            session["auto_enable_at"] = get_next_midnight() if auto_enable else None
            console.print(f"[yellow]⚠️ Session {session_id[:8]}... 已禁用至次日凌晨 00:00[/yellow]")
        elif session["fail_count"] >= session_pool["settings"]["max_fail_count"]:
            session["enabled"] = False
            session["disabled_at"] = now_str
            session["auto_enable_at"] = get_next_midnight() if auto_enable else None
            console.print(f"[yellow]⚠️ Session {session_id[:8]}... 失败次数达到阈值，已自动禁用[/yellow]")
        
        # 保存并推送更新
        save_data()
        await broadcast_session_update()


async def remove_session(session_id: str):
    """从账号池中删除 session（sessionid 过期）"""
    if session_id in session_pool["sessions"]:
        session_email = session_pool["sessions"][session_id].get("email", "unknown")
        del session_pool["sessions"][session_id]
        console.print(f"[red]🗑️ Session {session_id[:8]}... ({session_email}) 已从账号池删除（sessionid 过期）[/red]")
        # 保存并推送更新
        save_data()
        await broadcast_session_update()


def get_today_start() -> datetime:
    """获取今天凌晨 00:00 的时间"""
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def check_and_auto_enable_sessions():
    """检查并自动解禁到期的 session（过了凌晨 00:00 解禁所有禁用的账户）"""
    if not session_pool["settings"].get("auto_enable_at_midnight", True):
        return

    today_start = get_today_start()
    enabled_count = 0

    for sid, session in session_pool["sessions"].items():
        if not session["enabled"]:
            # 检查是否有 auto_enable_at 且已过期
            auto_enable = session.get("auto_enable_at")
            if auto_enable:
                auto_enable_time = datetime.strptime(auto_enable, "%Y-%m-%d %H:%M:%S")
                if today_start >= auto_enable_time:
                    session["enabled"] = True
                    session["fail_count"] = 0
                    session["auto_enable_at"] = None
                    session["disabled_at"] = None
                    enabled_count += 1
                    console.print(f"[green]✅ Session {sid[:8]}... 已自动解禁[/green]")
            else:
                # 没有 auto_enable_at 的禁用账户，检查是否是今天之前禁用的
                # 如果是今天之前禁用的，过了 00:00 也解禁
                disabled_at = session.get("disabled_at")
                if disabled_at:
                    disabled_time = datetime.strptime(disabled_at, "%Y-%m-%d %H:%M:%S")
                    if disabled_time < today_start:
                        session["enabled"] = True
                        session["fail_count"] = 0
                        session["disabled_at"] = None
                        enabled_count += 1
                        console.print(f"[green]✅ Session {sid[:8]}... 已自动解禁（过 00:00）[/green]")

    if enabled_count > 0:
        save_data()
        await broadcast_session_update()



def get_next_midnight() -> str:
    """获取次日凌晨 00:00 的时间字符串"""
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.strftime("%Y-%m-%d %H:%M:%S")

async def mark_session_success(session_id: str):
    """标记session成功使用"""
    if session_id in session_pool["sessions"]:
        session = session_pool["sessions"][session_id]
        session["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 可选：成功时重置失败计数
        # session["fail_count"] = 0
        save_data()


async def broadcast_session_update():
    """广播session状态更新"""
    await ws_manager.broadcast({
        "type": "session_update",
        "sessions": get_sessions_list(),
        "settings": session_pool["settings"]
    })


def get_sessions_list() -> list:
    """获取session列表（用于前端显示）"""
    return [
        {
            "session_id": sid,
            "email": info.get("email", "unknown"),
            "fail_count": info.get("fail_count", 0),
            "enabled": info.get("enabled", True),
            "last_used": info.get("last_used"),
            "created_at": info.get("created_at")
        }
        for sid, info in session_pool["sessions"].items()
    ]


# ============== 请求模型 ==============

class ImageUrl(BaseModel):
    url: str


class ContentPart(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[ImageUrl] = None


class Message(BaseModel):
    role: str
    content: Union[str, List[ContentPart]]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: Optional[bool] = True


# ============== 默认参数 ==============

DEFAULT_IMAGE_PARAMS = {
    "ratio": "9:16",
    "resolution": "2k",
    "sample_strength": 0.7
}

DEFAULT_VIDEO_PARAMS = {
    "ratio": "9:16",
    "resolution": "720p",
    "duration": 5
}

# 支持的参数列表
SUPPORTED_PARAMS = ["ratio", "resolution", "duration", "strength", "negative"]


# ============== 辅助函数 ==============

def parse_prompt_params(text: str) -> tuple[str, dict]:
    """
    解析prompt中的--参数
    示例: '一只猫 --ratio 9:16 --resolution 2k' -> ('一只猫', {'ratio': '9:16', 'resolution': '2k'})
    """
    params = {}
    prompt = text
    
    # 匹配 --key value 格式的参数
    # value可以是: 数字、比例(如9:16)、或不含空格的字符串
    pattern = r'--(\w+)\s+([^\s-]+(?:\:[^\s]+)?)'
    
    matches = re.findall(pattern, text)
    for key, value in matches:
        key_lower = key.lower()
        # 转换参数值类型
        if key_lower == "duration":
            try:
                params[key_lower] = int(value)
            except ValueError:
                params[key_lower] = value
        elif key_lower == "strength":
            try:
                params["sample_strength"] = float(value)
            except ValueError:
                params["sample_strength"] = value
        else:
            params[key_lower] = value
    
    # 移除prompt中的参数部分，只保留纯文本
    prompt = re.sub(r'\s*--\w+\s+[^\s-]+(?:\:[^\s]+)?', '', text).strip()
    
    return prompt, params


def determine_task_type(model: str, has_images: bool) -> str:
    """
    判断任务类型
    返回: text2image, image2image, text2video, image2video
    """
    is_video = "video" in model.lower()
    if is_video:
        return "image2video" if has_images else "text2video"
    return "image2image" if has_images else "text2image"


def extract_prompt_and_images(messages: List[Message]) -> tuple[str, List[str], dict]:
    """从消息中提取prompt、图片和参数"""
    text_parts = []
    images = []
    
    for msg in messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                text_parts.append(msg.content)
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if part.type == "text" and part.text:
                        text_parts.append(part.text)
                    elif part.type == "image_url" and part.image_url:
                        images.append(part.image_url.url)
    
    # 合并文本并解析参数
    full_text = " ".join(text_parts)
    prompt, params = parse_prompt_params(full_text)
    
    return prompt, images, params


def create_chunk(chunk_id: str, model: str, content: str, finish_reason: Optional[str] = None) -> str:
    """创建SSE chunk"""
    chunk = {
        "id": f"chatcmpl-{chunk_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "role": None,
                "content": content
            },
            "finish_reason": finish_reason
        }]
    }
    return f"data: {json.dumps(chunk)}\n\n"


async def log_request(request_id: str, model: str, prompt: str, status: str, 
                duration: float = 0, error: str = None, result_url: str = None,
                session_id: str = None):
    """记录并显示请求日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 更新统计
    call_stats["total_calls"] += 1
    if status == "success":
        call_stats["successful_calls"] += 1
    else:
        call_stats["failed_calls"] += 1
    
    # 获取session信息
    session_email = None
    if session_id and session_id in session_pool["sessions"]:
        session_email = session_pool["sessions"][session_id]["email"]
    
    # 保存历史记录
    record = {
        "id": request_id,
        "timestamp": timestamp,
        "model": model,
        "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt,
        "full_prompt": prompt,
        "status": status,
        "duration": f"{duration:.2f}s",
        "duration_seconds": duration,
        "error": error,
        "result_url": result_url,
        "session_id": session_id[:8] + "..." if session_id else None,
        "session_email": session_email
    }
    call_stats["calls_history"].append(record)
    
    # 保持最近100条记录
    if len(call_stats["calls_history"]) > 100:
        call_stats["calls_history"] = call_stats["calls_history"][-100:]
    
    # 打印到终端
    status_icon = "✅" if status == "success" else "❌"
    session_info = f" | {session_id[:8]}..." if session_id else ""
    console.print(f"{status_icon} [{timestamp}] {request_id} | {model}{session_info} | {record['prompt'][:40]}... | {duration:.2f}s")
    
    # 推送到WebSocket客户端
    await ws_manager.broadcast({
        "type": "new_call",
        "record": record,
        "stats": {
            "total_calls": call_stats["total_calls"],
            "successful_calls": call_stats["successful_calls"],
            "failed_calls": call_stats["failed_calls"]
        }
    })


# ============== API 端点 ==============

@app.get("/")
async def root():
    """Dashboard 页面"""
    return FileResponse(DASHBOARD_FILE)


@app.get("/api/info")
async def api_info():
    """API信息"""
    return {
        "service": "即梦 OpenAI 兼容接口",
        "version": "1.0.0",
        "jimeng_backend": session_pool["settings"]["jimeng_base_url"],
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "stats": "/stats"
        }
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket连接，用于实时推送调用记录和session状态"""
    await ws_manager.connect(websocket)
    try:
        # 发送初始数据
        await websocket.send_json({
            "type": "init",
            "backend_url": session_pool["settings"]["jimeng_base_url"],
            "stats": {
                "total_calls": call_stats["total_calls"],
                "successful_calls": call_stats["successful_calls"],
                "failed_calls": call_stats["failed_calls"]
            },
            "history": list(reversed(call_stats["calls_history"])),
            "sessions": get_sessions_list(),
            "settings": session_pool["settings"]
        })
        
        # 保持连接
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.get("/v1/models")
async def list_models():
    """列出可用模型"""
    created = int(time.time())
    
    # 图片模型
    image_models = [
        {"id": "jimeng-4.5", "type": "image"},
        {"id": "jimeng-4.1", "type": "image"},
        {"id": "jimeng-4.0", "type": "image"},
        {"id": "jimeng-3.0", "type": "image"},
    ]
    
    # 视频模型
    video_models = [
        {"id": "jimeng-video-3.5-pro", "type": "video"},
        {"id": "jimeng-video-3.0", "type": "video"},
        {"id": "jimeng-video-3.0-pro", "type": "video"},
        {"id": "jimeng-video-3.0-fast", "type": "video"},
        {"id": "jimeng-video-2.0-pro", "type": "video"},
        {"id": "jimeng-video-2.0", "type": "video"},
        {"id": "jimeng-video-veo3", "type": "video"},
        {"id": "jimeng-video-veo3.1", "type": "video"},
        {"id": "jimeng-video-sora2", "type": "video"},
    ]
    
    all_models = image_models + video_models
    
    return {
        "object": "list",
        "data": [
            {
                "id": m["id"],
                "object": "model",
                "created": created,
                "owned_by": "jimeng",
                "type": m["type"]
            }
            for m in all_models
        ]
    }


@app.get("/stats")
async def get_stats():
    """获取调用统计"""
    return {
        "total_calls": call_stats["total_calls"],
        "successful_calls": call_stats["successful_calls"],
        "failed_calls": call_stats["failed_calls"],
        "success_rate": f"{call_stats['successful_calls']/call_stats['total_calls']*100:.1f}%" if call_stats["total_calls"] > 0 else "N/A",
        "recent_calls": call_stats["calls_history"][-10:]
    }


@app.post("/stats/clear")
async def clear_stats():
    """清空调用统计"""
    call_stats["total_calls"] = 0
    call_stats["successful_calls"] = 0
    call_stats["failed_calls"] = 0
    call_stats["calls_history"] = []
    
    # 通知所有WebSocket客户端
    await ws_manager.broadcast({
        "type": "init",
        "backend_url": JIMENG_BASE_URL,
        "stats": {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0
        },
        "sessions": get_sessions_list(),
        "settings": session_pool["settings"],
        "history": []
    })
    
    return {"status": "ok"}


# ============== Session管理端点 ==============

class SessionImportRequest(BaseModel):
    content: str


class SettingsUpdateRequest(BaseModel):
    max_fail_count: Optional[int] = None
    auto_enable_at_midnight: Optional[bool] = None
    jimeng_base_url: Optional[str] = None
    session_prefix: Optional[str] = None


@app.get("/api/sessions")
async def get_sessions():
    """获取所有session列表"""
    return {
        "sessions": get_sessions_list(),
        "settings": session_pool["settings"],
        "available_count": len([
            s for s in session_pool["sessions"].values()
            if s["enabled"] and s["fail_count"] < session_pool["settings"]["max_fail_count"]
        ])
    }


@app.post("/api/sessions/import")
async def import_sessions_endpoint(request: SessionImportRequest):
    """导入session列表"""
    result = import_sessions(request.content)
    console.print(f"[green]✅ 导入Sessions: 新增 {result['imported']}, 跳过 {result['skipped']}, 总计 {result['total']}[/green]")
    
    # 广播更新
    await broadcast_session_update()
    
    return result


@app.post("/api/sessions/{session_id}/toggle")
async def toggle_session(session_id: str):
    """切换session启用/禁用状态"""
    if session_id not in session_pool["sessions"]:
        raise HTTPException(status_code=404, detail="Session不存在")
    
    session = session_pool["sessions"][session_id]
    session["enabled"] = not session["enabled"]
    
    # 如果重新启用，重置失败计数和禁用时间
    if session["enabled"]:
        session["fail_count"] = 0
        session["disabled_at"] = None
        session["auto_enable_at"] = None
    
    status = "启用" if session["enabled"] else "禁用"
    console.print(f"[cyan]🔄 Session {session_id[:8]}... 已{status}[/cyan]")
    
    # 保存并广播更新
    save_data()
    await broadcast_session_update()
    
    return {"status": "ok", "enabled": session["enabled"]}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除session"""
    if session_id not in session_pool["sessions"]:
        raise HTTPException(status_code=404, detail="Session不存在")
    
    del session_pool["sessions"][session_id]
    console.print(f"[red]🗑️ Session {session_id[:8]}... 已删除[/red]")
    
    # 保存并广播更新
    save_data()
    await broadcast_session_update()
    
    return {"status": "ok"}


@app.post("/api/sessions/enable-all")
async def enable_all_sessions():
    """解禁所有禁用的 session"""
    enabled_count = 0
    for session in session_pool["sessions"].values():
        if not session["enabled"]:
            session["enabled"] = True
            session["fail_count"] = 0
            session["disabled_at"] = None
            session["auto_enable_at"] = None
            enabled_count += 1
    console.print(f"[green]✅ 已解禁 {enabled_count} 个 Session[/green]")

    # 保存并广播更新
    save_data()
    await broadcast_session_update()

    return {"status": "ok", "enabled_count": enabled_count}


@app.post("/api/sessions/clear")
async def clear_sessions():
    """清空所有session"""
    session_pool["sessions"] = {}
    console.print("[red]🗑️ 所有Sessions已清空[/red]")
    
    # 保存并广播更新
    save_data()
    await broadcast_session_update()
    
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings():
    """获取设置"""
    return session_pool["settings"]


@app.post("/api/settings")
async def update_settings(request: SettingsUpdateRequest):
    """更新设置"""
    if request.max_fail_count is not None:
        session_pool["settings"]["max_fail_count"] = request.max_fail_count
        console.print(f"[cyan]⚙️ 设置已更新: max_fail_count = {request.max_fail_count}[/cyan]")
    
    if request.jimeng_base_url is not None:
        session_pool["settings"]["jimeng_base_url"] = request.jimeng_base_url.rstrip('/')
        console.print(f"[cyan]⚙️ 设置已更新: jimeng_base_url = {request.jimeng_base_url}[/cyan]")
    
    if request.session_prefix is not None:
        session_pool["settings"]["session_prefix"] = request.session_prefix
        console.print(f"[cyan]⚙️ 设置已更新: session_prefix = {request.session_prefix}[/cyan]")

    if request.auto_enable_at_midnight is not None:
        session_pool["settings"]["auto_enable_at_midnight"] = request.auto_enable_at_midnight
        console.print(f"[cyan]⚙️ 设置已更新: auto_enable_at_midnight = {request.auto_enable_at_midnight}[/cyan]")
    
    # 保存并广播更新
    save_data()
    await broadcast_session_update()
    
    return {"status": "ok", "settings": session_pool["settings"]}


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None)
):
    # 每次请求前检查并解禁过期的 session
    await check_and_auto_enable_sessions()
    """
    OpenAI 兼容的 Chat Completions 接口
    支持文生图、图生图、文生视频、图生视频
    """
    request_id = str(uuid.uuid4())[:12]
    start_time = time.time()
    
    # 获取session：优先使用session池，否则使用Authorization header
    selected_session = get_random_session()
    if selected_session:
        session_id, session_info = selected_session
        prefix = session_pool["settings"].get("session_prefix", "hk-")
        api_key = f"{prefix}{session_id}"
        console.print(f"[dim]   使用Session: {prefix}{session_id[:8]}... ({session_info['email']})[/dim]")
    else:
        session_id = None
        api_key = ""
        if authorization:
            api_key = authorization.replace("Bearer ", "")
        if not api_key and not session_pool["sessions"]:
            pass
        elif not api_key:
            raise HTTPException(status_code=503, detail="没有可用的Session，请导入Session或稍后重试")
    
    # 提取 prompt、图片和参数
    prompt, images, user_params = extract_prompt_and_images(request.messages)
    
    if not prompt:
        raise HTTPException(status_code=400, detail="消息中未找到有效的提示词")
    
    # 判断任务类型
    task_type = determine_task_type(request.model, bool(images))
    is_video = task_type in ["text2video", "image2video"]
    
    # 合并默认参数和用户参数
    default_params = DEFAULT_VIDEO_PARAMS.copy() if is_video else DEFAULT_IMAGE_PARAMS.copy()
    params = {**default_params, **user_params}
    
    console.print(f"\n[bold blue]🚀 收到新请求[/bold blue] ID: {request_id}")
    console.print(f"   任务类型: {task_type}, 模型: {request.model}")
    console.print(f"   提示词: {prompt[:50]}...")
    console.print(f"   参数: {params}")
    
    async def generate_stream():
        """生成流式响应"""
        nonlocal start_time
        result_url = None
        error_msg = None
        
        try:
            yield create_chunk(request_id, request.model, f"[开始{task_type}...]\n")
            
            jimeng_url = session_pool["settings"]["jimeng_base_url"]
            
            # 根据任务类型构建请求
            if task_type == "text2image":
                # 文生图: POST /v1/images/generations
                endpoint = f"{jimeng_url}/v1/images/generations"
                jimeng_payload = {
                    "model": request.model if request.model.startswith("jimeng") else "jimeng-4.5",
                    "prompt": prompt,
                    "ratio": params.get("ratio", "9:16"),
                    "resolution": params.get("resolution", "2k")
                }
                
            elif task_type == "image2image":
                # 图生图: POST /v1/images/compositions
                endpoint = f"{jimeng_url}/v1/images/compositions"
                jimeng_payload = {
                    "model": request.model if request.model.startswith("jimeng") else "jimeng-4.5",
                    "prompt": prompt,
                    "images": images,
                    "ratio": params.get("ratio", "9:16"),
                    "resolution": params.get("resolution", "2k"),
                    "sample_strength": params.get("sample_strength", 0.7)
                }
                if params.get("negative"):
                    jimeng_payload["negative_prompt"] = params["negative"]
                    
            elif task_type in ["text2video", "image2video"]:
                # 视频生成: POST /v1/videos/generations
                endpoint = f"{jimeng_url}/v1/videos/generations"
                jimeng_payload = {
                    "model": request.model if request.model.startswith("jimeng") else "jimeng-video-3.5-pro",
                    "prompt": prompt,
                    "ratio": params.get("ratio", "9:16"),
                    "resolution": params.get("resolution", "720p"),
                    "duration": params.get("duration", 5)
                }
                # 图生视频时添加图片
                if images:
                    jimeng_payload["file_paths"] = images
            
            yield create_chunk(request_id, request.model, "[请求已发送到即梦服务...]\n")
            
            async with httpx.AsyncClient(timeout=600.0) as client:  # 视频生成可能需要更长时间
                response = await client.post(
                    endpoint,
                    json=jimeng_payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    }
                )
                
                if response.status_code != 200:
                    error_msg = f"即梦API返回错误: {response.status_code} - {response.text[:200] if response.text else 'empty'}"
                    yield create_chunk(request_id, request.model, f"[错误] {error_msg}\n", "stop")
                    await log_request(request_id, request.model, prompt, "failed", 
                               time.time() - start_time, error_msg, session_id=session_id)
                    if session_id:
                        await mark_session_failed(session_id)
                    yield "data: [DONE]\n\n"
                    return
                
                try:
                    result = response.json()
                except Exception as parse_err:
                    error_msg = f"解析响应失败: {parse_err}, 原始响应: {response.text[:200] if response.text else 'empty'}"
                    yield create_chunk(request_id, request.model, f"[错误] {error_msg}\n", "stop")
                    await log_request(request_id, request.model, prompt, "failed",
                               time.time() - start_time, error_msg, session_id=session_id)
                    if session_id:
                        await mark_session_failed(session_id)
                    yield "data: [DONE]\n\n"
                    return
                
                console.print(f"[dim]   即梦响应: {json.dumps(result, ensure_ascii=False)[:300]}...[/dim]")
                
                # 检查业务错误码
                if isinstance(result, dict) and result.get("code") is not None and result.get("code") != 0:
                    error_msg = result.get("message", f"业务错误码: {result.get('code')}")
                    yield create_chunk(request_id, request.model, f"[错误] {error_msg}\n", "stop")
                    await log_request(request_id, request.model, prompt, "failed",
                               time.time() - start_time, error_msg, session_id=session_id)
                    # 错误码 1015 或 34010105 表示 sessionid 过期，直接从账号池删除
                    if session_id and result.get("code") in [1015, 34010105]:
                        await remove_session(session_id)
                    # 错误码 121101 直接禁用至次日凌晨 00:00
                    elif session_id and result.get("code") == 121101:
                        session_pool["sessions"][session_id]["enabled"] = False
                        session_pool["sessions"][session_id]["disabled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        if session_pool["settings"].get("auto_enable_at_midnight", True):
                            session_pool["sessions"][session_id]["auto_enable_at"] = get_next_midnight()
                            console.print(f"[red]❌ Session {session_id[:8]}... 错误码 121101，已禁用（将于次日凌晨 00:00 自动解禁）[/red]")
                        save_data()
                        await broadcast_session_update()
                    elif session_id and result.get("code") in [-2002, -2001, -1001, 401, 403]:
                        await mark_session_failed(session_id, disable_until_midnight=True)
                    yield "data: [DONE]\n\n"
                    return
                
                # 解析响应获取URL
                if isinstance(result, dict):
                    data = result.get("data")
                    if data and isinstance(data, list) and len(data) > 0:
                        result_url = data[0].get("url", "") or data[0].get("video_url", "") or data[0].get("image_url", "")
                    elif isinstance(data, dict):
                        result_url = data.get("url", "") or data.get("video_url", "") or data.get("image_url", "")
                    elif result.get("url"):
                        result_url = result["url"]
                    elif result.get("video_url"):
                        result_url = result["video_url"]
                    elif result.get("image_url"):
                        result_url = result["image_url"]
                
                if result_url:
                    yield create_chunk(request_id, request.model, "[生成完成!]\n")
                    # 根据任务类型输出不同格式
                    if is_video:
                        output = f'<video src="{result_url}" controls></video>'
                    else:
                        output = f"![Generated Image]({result_url})"
                    yield create_chunk(request_id, request.model, output, "stop")
                    await log_request(request_id, request.model, prompt, "success",
                               time.time() - start_time, result_url=result_url, session_id=session_id)
                    if session_id:
                        await mark_session_success(session_id)
                else:
                    yield create_chunk(request_id, request.model, 
                                      f"生成结果: {json.dumps(result, ensure_ascii=False)}", "stop")
                    await log_request(request_id, request.model, prompt, "success",
                               time.time() - start_time, session_id=session_id)
                    if session_id:
                        await mark_session_success(session_id)
                
        except httpx.TimeoutException:
            error_msg = "请求超时"
            yield create_chunk(request_id, request.model, f"[错误] {error_msg}\n", "stop")
            await log_request(request_id, request.model, prompt, "failed",
                       time.time() - start_time, error_msg, session_id=session_id)
            if session_id:
                await mark_session_failed(session_id)
        except Exception as e:
            error_msg = str(e)
            yield create_chunk(request_id, request.model, f"[错误] {error_msg}\n", "stop")
            await log_request(request_id, request.model, prompt, "failed",
                       time.time() - start_time, error_msg, session_id=session_id)
            if session_id:
                await mark_session_failed(session_id)
        
        yield "data: [DONE]\n\n"
    
    if request.stream:
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        # 非流式响应
        chunks = []
        async for chunk in generate_stream():
            if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                try:
                    data = json.loads(chunk[6:])
                    content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        chunks.append(content)
                except:
                    pass
        
        return {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(chunks)
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(prompt),
                "completion_tokens": len("".join(chunks)),
                "total_tokens": len(prompt) + len("".join(chunks))
            }
        }


# ============== 启动 ==============

@app.on_event("startup")
async def on_startup():
    """应用启动时初始化"""
    load_data()


def print_startup_banner():
    """打印启动横幅"""
    console.print("\n")
    console.print(f"[bold cyan]🎨 即梦 OpenAI 兼容接口[/bold cyan]")
    console.print(f"[bold]   Dashboard:[/bold] http://localhost:{SERVER_PORT}")
    console.print(f"[bold]   即梦后端:[/bold]  {session_pool['settings']['jimeng_base_url']}")
    console.print(f"[dim]   API端点:    POST /v1/chat/completions[/dim]")
    console.print("\n[dim]等待请求中...[/dim]\n")


if __name__ == "__main__":
    import uvicorn
    print_startup_banner()
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, log_level="warning")
