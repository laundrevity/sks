# backend/server.py
import os
import json
import asyncio
from typing import Dict, Optional, Any

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError

from glial.agent import Agent
from glial.streaming import Delta
from storage import Storage

# --------------------------------------------------------------------------------------
# Globals
# --------------------------------------------------------------------------------------
SESSIONS: Dict[str, Agent] = {}  # per-session agents in memory
DB_PATH = os.getenv("DB_PATH", "./data/app.sqlite3")
STORE = Storage(DB_PATH)

# --------------------------------------------------------------------------------------
# CORS helpers
# --------------------------------------------------------------------------------------
def cors_headers_for(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Credentials": "true",
    }

@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors_headers_for(request))
    try:
        resp = await handler(request)
    except web.HTTPException as ex:
        for k, v in cors_headers_for(request).items():
            ex.headers[k] = v
        raise
    for k, v in cors_headers_for(request).items():
        resp.headers.setdefault(k, v)
    return resp

# --------------------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------------------
async def health(_request: web.Request):
    return web.json_response({"ok": True})

# --------------------------------------------------------------------------------------
# Conversations API
# --------------------------------------------------------------------------------------
async def list_conversations(request: web.Request):
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    data = STORE.list_conversations(limit=limit, offset=offset)
    return web.json_response({"conversations": data})

async def create_conversation(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = body.get("title")
    settings = body.get("settings")
    conv_id = STORE.create_conversation(title=title, settings=settings)
    conv = STORE.get_conversation(conv_id) or {"id": conv_id, "title": title}
    return web.json_response(conv, status=201)

async def get_conversation(request: web.Request):
    conv_id = request.match_info["conv_id"]
    conv = STORE.get_conversation(conv_id)
    if not conv:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "not found"}),
            content_type="application/json",
        )
    return web.json_response(conv)

async def patch_conversation(request: web.Request):
    conv_id = request.match_info["conv_id"]
    conv = STORE.get_conversation(conv_id)
    if not conv:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "not found"}),
            content_type="application/json",
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = body.get("title", None)
    settings = body.get("settings", None)

    updates = []
    params: list[Any] = []
    if title is not None:
        updates.append("title=?")
        params.append(title)
    if settings is not None:
        updates.append("settings=?")
        params.append(json.dumps(settings, ensure_ascii=False))
    if updates:
        import time as _t
        updates.append("updated_at=?")
        params.append(int(_t.time()))
        STORE.conn.execute(f"UPDATE conversations SET {', '.join(updates)} WHERE id=?", (*params, conv_id))

    conv2 = STORE.get_conversation(conv_id) or {"id": conv_id, "title": title}
    return web.json_response(conv2)

# --------------------------------------------------------------------------------------
# Shared SSE streaming helper
# --------------------------------------------------------------------------------------
async def _stream_round(request: web.Request, session_id: str, prompt: str, conv_id: Optional[str] = None):
    if os.getenv("OPENAI_API_KEY", "").strip() == "":
        return web.json_response({"error": "OPENAI_API_KEY not set"}, status=500)

    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            **cors_headers_for(request),
        },
    )
    await resp.prepare(request)

    client_open = True
    saw_any_stream = False
    saw_completed = False

    async def emit(d: Delta):
        nonlocal client_open, saw_any_stream, saw_completed
        if not client_open:
            return

        # Proxy upstream deltas to the client
        payload = {
            "kind": d.kind,
            "output_index": d.output_index,
            "item_id": d.item_id,
            "content_index": d.content_index,
            "summary_index": d.summary_index,
            "text": d.text,
            "name": d.name,
            "call_id": d.call_id,
            "status": d.status,
            "meta": d.meta or {},
        }

        try:
            await resp.write(f"event: {d.kind}\n".encode("utf-8"))
            await resp.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            saw_any_stream = True
            if d.kind == "response.status" and d.status == "completed":
                saw_completed = True
        except (ConnectionResetError, ClientConnectionError, RuntimeError):
            client_open = False

    # get or create agent; seed from DB if conv-based and first time
    agent = SESSIONS.get(session_id)
    if agent is None:
        agent = Agent(on_delta=emit)
        await agent.__aenter__()
        if conv_id:
            try:
                agent.items = STORE.get_items_for_agent(conv_id)
            except Exception:
                agent.items = []
        SESSIONS[session_id] = agent
    else:
        agent.on_delta = emit  # rebind per-HTTP-connection

    try:
        ret = await agent(prompt)

        total_tokens: Optional[int] = None
        new_items: Optional[list] = None
        if isinstance(ret, dict):
            total_tokens = ret.get("total_tokens")
            new_items = ret.get("new_items")
        else:
            total_tokens = ret

        if conv_id and new_items:
            try:
                STORE.append_messages(conv_id, new_items)
            except Exception:
                # don't break streaming on persistence errors
                pass

        if client_open and total_tokens is not None:
            usage_payload = {"kind": "response.usage", "total_tokens": total_tokens}
            try:
                await resp.write(b"event: response.usage\n")
                await resp.write(f"data: {json.dumps(usage_payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            except (ConnectionResetError, ClientConnectionError, RuntimeError):
                client_open = False

    except asyncio.CancelledError:
        # client disconnected mid-stream
        pass
    except Exception as e:
        # Only surface as SSE 'error' if we haven't already completed or streamed output
        if client_open and not saw_completed and not saw_any_stream:
            err = {"message": str(e)}
            try:
                await resp.write(b"event: error\n")
                await resp.write(f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode("utf-8"))
            except Exception:
                pass
        # otherwise, swallow the error to avoid noisy bubbles for harmless post-completion issues
    finally:
        try:
            await resp.write_eof()
        except Exception:
            pass

    return resp

# --------------------------------------------------------------------------------------
# Stream endpoints
# --------------------------------------------------------------------------------------
async def stream_chat(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    prompt = (data.get("prompt") or "").strip()
    session_id = (data.get("session") or "default").strip() or "default"
    if not prompt:
        return web.json_response({"error": "prompt required"}, status=400)

    return await _stream_round(request, session_id=session_id, prompt=prompt, conv_id=None)

async def stream_chat_conversation(request: web.Request):
    conv_id = request.match_info["conv_id"]
    if not STORE.get_conversation(conv_id):
        raise web.HTTPNotFound(
            text=json.dumps({"error": "not found"}),
            content_type="application/json",
        )

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt required"}, status=400)

    return await _stream_round(request, session_id=conv_id, prompt=prompt, conv_id=conv_id)

# --------------------------------------------------------------------------------------
# App wiring
# --------------------------------------------------------------------------------------
def create_app():
    app = web.Application(middlewares=[cors_mw])
    app.add_routes([
        web.get("/healthz", health),

        # Conversations
        web.get("/v1/conversations", list_conversations),
        web.post("/v1/conversations", create_conversation),
        web.get("/v1/conversations/{conv_id}", get_conversation),
        web.patch("/v1/conversations/{conv_id}", patch_conversation),

        # Streaming (session-scoped)
        web.post("/v1/stream", stream_chat),
        web.options("/v1/stream", stream_chat),

        # Streaming (conversation-scoped)
        web.post("/v1/conversations/{conv_id}/stream", stream_chat_conversation),
        web.options("/v1/conversations/{conv_id}/stream", stream_chat_conversation),
    ])
    return app

if __name__ == "__main__":
    web.run_app(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )

