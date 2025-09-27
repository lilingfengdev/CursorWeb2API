import asyncio
import json
import logging
import time
import uuid
import os
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# --- Pydantic 模型以兼容 OpenAI ---

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False

class ModelCard(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "unknown"
    created: int = int(time.time())

class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelCard]

# --- 基本设置 ---

# 从环境变量中读取 API 密钥，如果未设置则使用默认值
API_KEY = os.environ.get("CURSOR_API_KEY", "your-default-api-key")
if API_KEY == "your-default-api-key":
    print("\033[93m" + "警告: 正在使用默认 API 密钥。请设置 CURSOR_API_KEY 环境变量以确保安全。" + "\033[0m")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def verify_api_key(token: str = Depends(oauth2_scheme)):
    """依赖项：验证 API 密钥"""
    if token != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="无效的 API 密钥",
            headers={"WWW-Authenticate": "Bearer"},
        )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Cursor OpenAI-Compatible API Bridge",
    description="一个通过 WebSocket 桥接浏览器脚本，将 Cursor 服务转换为 OpenAI 兼容 API 的服务。",
    version="1.0.0"
)

# --- CORS 中间件 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)


# --- 模型列表 ---
# 基于原始脚本和当前 Cursor 支持的模型列表
AVAILABLE_MODELS = [
    # GPT-5 系列
    {"id": "gpt-5", "owned_by": "openai"},
    {"id": "gpt-5-codex", "owned_by": "openai"},
    {"id": "gpt-5-mini", "owned_by": "openai"},
    {"id": "gpt-5-nano", "owned_by": "openai"},

    # GPT-4.1 系列
    {"id": "gpt-4.1", "owned_by": "openai"},
    {"id": "gpt-4o", "owned_by": "openai"},

    # Claude 系列
    {"id": "claude-3.5-sonnet", "owned_by": "anthropic"},
    {"id": "claude-3.5-haiku", "owned_by": "anthropic"},
    {"id": "claude-3.7-sonnet", "owned_by": "anthropic"},
    {"id": "claude-4-sonnet", "owned_by": "anthropic"},
    {"id": "claude-4-opus", "owned_by": "anthropic"},
    {"id": "claude-4.1-opus", "owned_by": "anthropic"},
    
    # Gemini 2.5 系列
    {"id": "gemini-2.5-pro", "owned_by": "google"},
    {"id": "gemini-2.5-flash", "owned_by": "google"},

    # 其他模型
    {"id": "o3", "owned_by": "openai"},
    {"id": "o4-mini", "owned_by": "openai"},
    {"id": "deepseek-r1", "owned_by": "deepseek"},
    {"id": "deepseek-v3.1", "owned_by": "deepseek"},
    {"id": "kimi-k2-instruct", "owned_by": "moonshot-ai"},
    {"id": "grok-3", "owned_by": "xai"},
    {"id": "grok-3-mini", "owned_by": "xai"},
    {"id": "grok-4", "owned_by": "xai"},
]

# --- 连接和状态管理 ---

class ConnectionManager:
    def __init__(self):
        self.active_connection: WebSocket | None = None
        self.pending_requests: Dict[str, asyncio.Queue] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        if self.active_connection:
            logger.warning("新的浏览器脚本连接，将替换现有连接。")
            await self.active_connection.close(code=1012, reason="被新连接替换")
        self.active_connection = websocket
        logger.info("浏览器脚本已连接。")

    def disconnect(self, websocket: WebSocket):
        if self.active_connection is websocket:
            self.active_connection = None
            logger.info("浏览器脚本已断开连接。")
            for request_id, queue in self.pending_requests.items():
                error_msg = {"type": "error", "data": {"error": "WebSocket connection lost."}}
                queue.put_nowait(error_msg)
            self.pending_requests.clear()

    async def send_to_browser(self, message: dict):
        if self.active_connection:
            await self.active_connection.send_text(json.dumps(message))
        else:
            raise ConnectionError("没有活动的浏览器脚本连接。服务不可用。")

    def create_request_queue(self, request_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.pending_requests[request_id] = queue
        return queue

    def get_request_queue(self, request_id: str) -> asyncio.Queue | None:
        return self.pending_requests.get(request_id)

    def remove_request_queue(self, request_id: str):
        if request_id in self.pending_requests:
            del self.pending_requests[request_id]

manager = ConnectionManager()

# --- WebSocket 端点 ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            logger.debug(f"收到浏览器消息: {message}")

            if "data" in message and "request_id" in message["data"]:
                request_id = message["data"]["request_id"]
                queue = manager.get_request_queue(request_id)
                if queue:
                    await queue.put(message)
                else:
                    logger.warning(f"收到未知 request_id 的消息: {request_id}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")
        manager.disconnect(websocket)

# --- OpenAI 兼容的 API 端点 ---

@app.get("/v1/models", response_model=ModelList)
async def list_models(_: str = Depends(verify_api_key)):
    """返回可用模型列表"""
    return ModelList(
        data=[ModelCard(id=model["id"], owned_by=model["owned_by"]) for model in AVAILABLE_MODELS]
    )

async def stream_generator(request_id: str, model: str, queue: asyncio.Queue):
    try:
        while True:
            message = await queue.get()
            msg_type = message.get("type")
            data = message.get("data", {})

            if msg_type == "error":
                error_msg = data.get('error', '未知流错误')
                logger.error(f"请求 {request_id} 在流式传输中发生错误: {error_msg}")

                # 以 OpenAI 兼容的格式在流中返回错误信息
                error_chunk = {
                    "id": f"chatcmpl-{request_id}",
                    "object": "chat.completion.chunk",
                    "created": int(asyncio.get_event_loop().time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": f"\n\n[ERROR]: {error_msg}"},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                break

            if msg_type == "chat_done":
                break

            if msg_type == "chat_delta":
                chunk = {
                    "id": f"chatcmpl-{request_id}",
                    "object": "chat.completion.chunk",
                    "created": int(asyncio.get_event_loop().time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": data.get("content", "")},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n"

        final_chunk = {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion.chunk",
            "created": int(asyncio.get_event_loop().time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        manager.remove_request_queue(request_id)

async def non_stream_handler(request_id: str, model: str, queue: asyncio.Queue):
    full_content = ""
    usage_data = {}
    try:
        while True:
            message = await queue.get()
            msg_type = message.get("type")
            data = message.get("data", {})

            if msg_type == "error":
                error_msg = data.get('error', '未知内部错误')
                status_code = 400 if "not found" in error_msg.lower() else 500
                raise HTTPException(status_code=status_code, detail=error_msg)

            if msg_type == "chat_delta":
                full_content += data.get("content", "")
            elif msg_type == "chat_usage":
                usage_data = data.get("usage", {})
            elif msg_type == "chat_done":
                break
        
        return {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion",
            "created": int(asyncio.get_event_loop().time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_content},
                "finish_reason": "stop"
            }],
            "usage": usage_data
        }
    finally:
        manager.remove_request_queue(request_id)

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, _: str = Depends(verify_api_key)):
    if not manager.active_connection:
        raise HTTPException(status_code=503, detail="浏览器脚本未连接，服务不可用。")

    request_id = str(uuid.uuid4())
    
    queue = manager.create_request_queue(request_id)

    try:
        await manager.send_to_browser({
            "type": "chat_request",
            "data": {
                "request_id": request_id,
                "model": request.model,
                "messages": [msg.dict() for msg in request.messages]
            }
        })
    except ConnectionError as e:
        manager.remove_request_queue(request_id)
        raise HTTPException(status_code=503, detail=str(e))

    if request.stream:
        return StreamingResponse(stream_generator(request_id, request.model, queue), media_type="text/event-stream")
    else:
        response_data = await non_stream_handler(request_id, request.model, queue)
        return JSONResponse(content=response_data)

@app.get("/")
async def root():
    return {"status": "running", "browser_connected": manager.active_connection is not None}

# --- 主入口点 ---

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
