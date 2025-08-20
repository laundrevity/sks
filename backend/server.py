import os
import json
import asyncio
from typing import Dict, Optional

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError

from glial.agent import Agent
from glial.streaming import Delta

# Keep per-session agents in memory (simple; swap for Redis if needed later)
SESSIONS: Dict[str, Agent] = {}


# --- CORS utils --------------------------------------------------------------

def cors_headers_for(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Credentials": "true",
    }


@web.middleware
async def cors_mw(request, handler):
    # Let normal JSON endpoints benefit from CORS via middleware
    # (SSE will set headers inside handler before prepare()).
    if request.method == "OPTIONS":
        # Short-circuit generic preflight
        return web.Response(status=204, headers=cors_headers_for(request))
    resp = await handler(request)
    # If handler already prepared the response (SSE), headers are already sent.
    # For others, add CORS here:
    for k, v in cors_headers_for(request).items():
        # don't overwrite if already set
        resp.headers.setdefault(k, v)
    return resp


# --- Endpoints ---------------------------------------------------------------

async def health(_request: web.Request):
    return web.json_response({"ok": True})


async def options_stream(request: web.Request):
    # Dedicated preflight that doesn't try to parse JSON, avoids 400s
    headers = cors_headers_for(request)
    headers["Content-Length"] = "0"
    return web.Response(status=204, headers=headers)


async def stream_chat(request: web.Request):
    """
    POST /v1/stream
    body: { "prompt": "...", "session": "optional-id" }
    streams: SSE events with lines:
       event: <kind>\n
       data: <json>\n
       \n
    """
    if os.getenv("OPENAI_API_KEY", "").strip() == "":
        return web.json_response({"error": "OPENAI_API_KEY not set"}, status=500)

    # Parse body (don’t assume preflight comes here; OPTIONS handled earlier)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    prompt = (data.get("prompt") or "").strip()
    session_id = (data.get("session") or "default").strip() or "default"
    if not prompt:
        return web.json_response({"error": "prompt required"}, status=400)

    # Prepare SSE response. IMPORTANT: set CORS headers BEFORE prepare()
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let proxies buffer
            **cors_headers_for(request),
        },
    )
    await resp.prepare(request)

    # Track if the client bailed to stop writing further
    client_open = True

    # Delta -> SSE writer
    async def emit(d: Delta):
        nonlocal client_open
        if not client_open:
            return
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
        except (ConnectionResetError, ClientConnectionError, RuntimeError):
            # Transport closed or response finished; stop emitting
            client_open = False

    # Get or create agent for this session; ensure it uses THIS request's emitter
    agent = SESSIONS.get(session_id)
    if agent is None:
        agent = Agent(on_delta=emit)
        await agent.__aenter__()  # open aiohttp.ClientSession inside Agent
        SESSIONS[session_id] = agent
    else:
        # <-- THIS fixes "first request works, second doesn't"
        agent.on_delta = emit

    try:
        # Trigger the whole tool/function loop and conversation persistence
        await agent(prompt)
    except asyncio.CancelledError:
        # client disconnected
        pass
    except Exception as e:
        # Surface as an SSE 'error' event (best-effort)
        err = {"message": str(e)}
        try:
            if client_open:
                await resp.write(b"event: error\n")
                await resp.write(f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode("utf-8"))
        except Exception:
            pass
    finally:
        # End the SSE stream cleanly
        try:
            await resp.write_eof()
        except Exception:
            pass

    return resp


def create_app():
    app = web.Application(middlewares=[cors_mw])
    app.add_routes([
        web.get("/healthz", health),
        web.options("/v1/stream", options_stream),  # clean preflight
        web.post("/v1/stream", stream_chat),
    ])
    return app


if __name__ == "__main__":
    web.run_app(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )

