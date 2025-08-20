from typing import AsyncIterator, Optional, List, Dict, Any, Callable, Awaitable, Union
from dataclasses import dataclass, field
import inspect
import json

import msgspec

from glial.models import (
    StreamEvent, UnknownEvent, AnyDeltaCallback, AggregatedResponse, Delta,
    _MessageState, _ReasoningState, _FunctionCallState, _CustomToolCallState,
    ResponseCreated, ResponseInProgress, ResponseOutputItemAdded, ResponseContentPartAdded,
    ResponseOutputTextDelta, ResponseOutputTextDone, ResponseContentPartDone,
    ResponseReasoningSummaryTextDelta, ResponseReasoningSummaryTextDone,
    ResponseFunctionCallArgumentsDelta, ResponseFunctionCallArgumentsDone,
    ResponseCustomToolCallInputDelta, ResponseCustomToolCallInputDone,
    ResponseOutputItemDone, ResponseCompleted
)


class SSEDecoder:
    def __init__(self, encoding: str = "utf-8"):
        self._buffer = ""
        self._encoding = encoding
        self._cur_event: Optional[str] = None
        self._cur_data_lines: List[str] = []
        self._decoder = msgspec.json.Decoder(StreamEvent)

    async def iter_events(self, byte_iter: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]:
        async for chunk in byte_iter:
            text = chunk.decode(self._encoding, errors="replace")
            self._buffer += text

            while True:
                nl = self._buffer.find("\n")
                if nl == -1:
                    break
                line = self._buffer[:nl]
                self._buffer = self._buffer[nl + 1 :]

                # Blank line indicates end of event
                if line.strip() == "":
                    if self._cur_data_lines:
                        data_str = "\n".join(self._cur_data_lines)
                        try:
                            yield self._decoder.decode(data_str)
                        except msgspec.DecodeError:
                            data = json.loads(data_str)
                            yield UnknownEvent(type=data.get("type", "unknown"), __raw__=data)
                    self._cur_event = None
                    self._cur_data_lines = []
                    continue

                if line.startswith("event:"):
                    self._cur_event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    self._cur_data_lines.append(line[len("data:") :].lstrip())
                else:
                    if line.startswith(":"):
                        continue
                    self._cur_data_lines.append(line)


class ResponseAggregator:
    def __init__(self, on_delta: Optional[AnyDeltaCallback] = None):
        self.final = AggregatedResponse()
        self._on_delta = on_delta
        self._msg: Dict[str, _MessageState] = {}
        self._rsn: Dict[str, _ReasoningState] = {}
        self._fn: Dict[str, _FunctionCallState] = {}
        self._ct: Dict[str, _CustomToolCallState] = {}

    async def _emit(self, d: Delta):
        if self._on_delta is None:
            return
        res = self._on_delta(d)
        if inspect.isawaitable(res):
            await res

    async def stream_from(self, byte_iter: AsyncIterator[bytes]) -> AggregatedResponse:
        decoder = SSEDecoder()
        async for ev in decoder.iter_events(byte_iter):
            match ev:
                case ResponseCreated(response=resp):
                    self.final.response_id = resp.id
                    self.final.model = resp.model
                    self.final.status = resp.status
                    await self._emit(Delta(kind="response.status", status=resp.status))

                case ResponseInProgress(response=resp):
                    self.final.status = resp.status
                    await self._emit(Delta(kind="response.status", status=resp.status))

                case ResponseOutputItemAdded(output_index=oi, item=item):
                    t = item.get("type")
                    match t:
                        case "message":
                            st = _MessageState(id=item["id"], output_index=oi, role=item.get("role", "assistant"))
                            self._msg[item["id"]] = st
                            await self._emit(Delta(kind="item.started", output_index=oi, item_id=item["id"], meta={"type": "message"}))
                        case "reasoning":
                            st = _ReasoningState(id=item["id"], output_index=oi)
                            self._rsn[item["id"]] = st
                            await self._emit(Delta(kind="item.started", output_index=oi, item_id=item["id"], meta={"type": "reasoning"}))
                        case "function_call":
                            st = _FunctionCallState(
                                id=item["id"],
                                output_index=oi,
                                name=item.get("name", ""),
                                call_id=item.get("call_id", ""),
                            )
                            self._fn[item["id"]] = st
                            await self._emit(Delta(kind="item.started", output_index=oi, item_id=item["id"], name=st.name, call_id=st.call_id, meta={"type": "function_call"}))
                        case "custom_tool_call":
                            st = _CustomToolCallState(
                                id=item["id"],
                                output_index=oi,
                                name=item.get("name", ""),
                                call_id=item.get("call_id", ""),
                            )
                            self._ct[item["id"]] = st
                            await self._emit(Delta(kind="item.started", output_index=oi, item_id=item["id"], name=st.name, call_id=st.call_id, meta={"type": "custom_tool_call"}))
                        case _:
                            await self._emit(Delta(kind="item.started", output_index=oi, item_id=item.get("id"), meta={"type": t}))

                case ResponseContentPartAdded(item_id=item_id, output_index=oi, content_index=ci):
                    if (st := self._msg.get(item_id)) is not None:
                        st.parts.setdefault(ci, [])

                case ResponseOutputTextDelta(item_id=item_id, output_index=oi, content_index=ci, delta=chunk):
                    if (st := self._msg.get(item_id)) is not None:
                        st.parts.setdefault(ci, []).append(chunk)
                    self.final.text += chunk
                    await self._emit(Delta(kind="text", output_index=oi, item_id=item_id, content_index=ci, text=chunk))

                case ResponseOutputTextDone():
                    pass

                case ResponseContentPartDone():
                    pass

                case ResponseReasoningSummaryTextDelta(item_id=item_id, output_index=oi, summary_index=si, delta=chunk):
                    if (st := self._rsn.get(item_id)):
                        st.summaries.setdefault(si, []).append(chunk)
                    await self._emit(Delta(kind="reasoning", output_index=oi, item_id=item_id, summary_index=si, text=chunk))

                case ResponseReasoningSummaryTextDone(text=full_text):
                    self.final.reasoning_summaries.append(full_text)

                # Typed function/custom tool events (if available)
                case ResponseFunctionCallArgumentsDelta(item_id=item_id, output_index=oi, delta=chunk):
                    if (st := self._fn.get(item_id)):
                        st.chunks.append(chunk)
                    await self._emit(Delta(kind="function.arguments", output_index=oi, item_id=item_id, text=chunk))

                case ResponseFunctionCallArgumentsDone(item_id=item_id, output_index=oi, arguments=raw):  # type: ignore
                    st = self._fn.get(item_id)
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                    self.final.function_calls.append(
                        {
                            "id": item_id,
                            "output_index": oi,
                            "name": st.name if st else None,
                            "call_id": st.call_id if st else None,
                            "arguments": parsed,
                            "arguments_raw": raw,
                        }
                    )

                case ResponseCustomToolCallInputDelta(item_id=item_id, output_index=oi, delta=chunk):
                    if (st := self._ct.get(item_id)):
                        st.chunks.append(chunk)
                    await self._emit(Delta(kind="custom.input", output_index=oi, item_id=item_id, text=chunk))

                case ev if isinstance(ev, ResponseCustomToolCallInputDone):
                    st = self._ct.get(ev.item_id)
                    self.final.custom_tool_calls.append(
                        {
                            "id": ev.item_id,
                            "output_index": ev.output_index,
                            "name": st.name if st else None,
                            "call_id": st.call_id if st else None,
                            "input": ev.input
                        }
                    )

                case ResponseOutputItemDone(output_index=oi, item=item):
                    await self._emit(Delta(kind="item.completed", output_index=oi, item_id=item.get("id"), meta={"item": item}))

                case ResponseCompleted(response=resp):
                    self.final.snapshot = resp
                    self.final.response_id = resp.id
                    self.final.status = resp.status
                    self.final.model = resp.model
                    self.final.usage = resp.usage
                    await self._emit(Delta(kind="response.status", status=resp.status))

                # Fallback for when structs.py lacks the typed function/custom events
                case UnknownEvent(type=typ, __raw__=raw):
                    match typ:
                        case "response.function_call_arguments.delta":
                            item_id = raw.get("item_id")
                            oi = raw.get("output_index")
                            delta = raw.get("delta", "")
                            if (st := self._fn.get(item_id)) is not None:
                                st.chunks.append(delta)
                            await self._emit(Delta(kind="function.arguments", output_index=oi, item_id=item_id, text=delta))
                        case "response.function_call_arguments.done":
                            item_id = raw.get("item_id")
                            oi = raw.get("output_index")
                            arguments = raw.get("arguments", "")
                            st = self._fn.get(item_id)
                            try:
                                parsed = json.loads(arguments)
                            except Exception:
                                parsed = arguments
                            self.final.function_calls.append(
                                {
                                    "id": item_id,
                                    "output_index": oi,
                                    "name": st.name if st else None,   # type: ignore[attr-defined]
                                    "call_id": st.call_id if st else None,  # type: ignore[attr-defined]
                                    "arguments": parsed,
                                    "arguments_raw": arguments,
                                }
                            )
                        case "response.custom_tool_call_input.delta":
                            item_id = raw.get("item_id")
                            oi = raw.get("output_index")
                            delta = raw.get("delta", "")
                            if (st := self._ct.get(item_id)) is not None:
                                st.chunks.append(delta)
                            await self._emit(Delta(kind="custom.input", output_index=oi, item_id=item_id, text=delta))
                        case "response.custom_tool_call_input.done":
                            item_id = raw.get("item_id")
                            oi = raw.get("output_index")
                            inp = raw.get("input", "")
                            st = self._ct.get(item_id)
                            self.final.custom_tool_calls.append(
                                {
                                    "id": item_id,
                                    "output_index": oi,
                                    "name": st.name if st else None,   # type: ignore[attr-defined]
                                    "call_id": st.call_id if st else None,  # type: ignore[attr-defined]
                                    "input": inp,
                                }
                            )
                        case _:
                            # Forward other unknowns for debugging if desired
                            await self._emit(Delta(kind="unknown", meta=raw))

                case _:
                    # Unhandled event kind; ignore
                    pass

        return self.final


async def stream_response(byte_iter: AsyncIterator[bytes], on_delta: Optional[AnyDeltaCallback] = None) -> AggregatedResponse:
    agg = ResponseAggregator(on_delta=on_delta)
    return await agg.stream_from(byte_iter)

