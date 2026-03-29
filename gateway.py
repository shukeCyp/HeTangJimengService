"""
统一网关服务 - 路由分发
/image/v1/* -> 图片服务
/video/v1/* -> 视频服务
"""
import asyncio

import httpx
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, RedirectResponse

app = FastAPI(title="Jimeng Gateway")

IMAGE_SERVICE = "http://image-service:18765"
VIDEO_SERVICE = "http://video-service:18765"
IMAGE_WS_SERVICE = "ws://image-service:18765/ws"
VIDEO_WS_SERVICE = "ws://video-service:18765/ws"

@app.get("/")
async def root():
    return RedirectResponse(url="/image/")

@app.get("/image/")
async def image_admin(request: Request):
    return await proxy_request(f"{IMAGE_SERVICE}/", request)

@app.get("/video/")
async def video_admin(request: Request):
    return await proxy_request(f"{VIDEO_SERVICE}/", request)

@app.api_route("/image/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def image_proxy(path: str, request: Request):
    return await proxy_request(f"{IMAGE_SERVICE}/{path}", request)

@app.api_route("/video/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def video_proxy(path: str, request: Request):
    return await proxy_request(f"{VIDEO_SERVICE}/{path}", request)


@app.websocket("/image/ws")
async def image_ws_proxy(websocket: WebSocket):
    await proxy_websocket(IMAGE_WS_SERVICE, websocket)


@app.websocket("/video/ws")
async def video_ws_proxy(websocket: WebSocket):
    await proxy_websocket(VIDEO_WS_SERVICE, websocket)

async def proxy_request(url: str, request: Request):
    async with httpx.AsyncClient() as client:
        headers = dict(request.headers)
        headers.pop("host", None)

        req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=await request.body(),
            params=request.query_params
        )

        resp = await client.send(req, stream=True)

        if "text/event-stream" in resp.headers.get("content-type", ""):
            return StreamingResponse(
                resp.aiter_raw(),
                status_code=resp.status_code,
                headers=dict(resp.headers),
                media_type="text/event-stream"
            )

        return Response(
            content=await resp.aread(),
            status_code=resp.status_code,
            headers=dict(resp.headers)
        )


async def proxy_websocket(target_url: str, client_ws: WebSocket):
    await client_ws.accept()

    try:
        # Dashboard 首包会携带统计和 session 列表，视频侧数据量大时可能超过
        # websockets 客户端默认的 1MB 限制，导致网关误判为连接失败。
        async with websockets.connect(target_url, max_size=None) as upstream_ws:
            async def client_to_upstream():
                while True:
                    message = await client_ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream_ws.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream_ws.send(message["bytes"])

            async def upstream_to_client():
                async for message in upstream_ws:
                    if isinstance(message, bytes):
                        await client_ws.send_bytes(message)
                    else:
                        await client_ws.send_text(message)

            tasks = [
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"WebSocket proxy error ({target_url}): {exc}")
        try:
            await client_ws.close()
        except Exception:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18566)
