from typing import Any, Dict, List, Literal, Optional, Union, Callable, Awaitable
from dataclasses import dataclass, field
import msgspec


# ---------- Base ----------
class KWStruct(msgspec.Struct, kw_only=True):
    """Keyword-only base to avoid required/optional ordering pitfalls."""
    pass


# ---------- Top-level response snapshot ----------
class ResponseCore(KWStruct):
    id: str
    object: Literal["response"]
    created_at: int
    status: str
    background: bool

    # Keep everything else flexible to tolerate schema drift
    error: Optional[Any] = None
    incomplete_details: Optional[Any] = None
    instructions: Optional[Any] = None
    max_output_tokens: Optional[int] = None
    max_tool_calls: Optional[int] = None
    model: Optional[str] = None
    output: Optional[List[Dict[str, Any]]] = None
    parallel_tool_calls: Optional[bool] = None
    previous_response_id: Optional[str] = None
    prompt_cache_key: Optional[str] = None
    reasoning: Optional[Dict[str, Any]] = None
    safety_identifier: Optional[str] = None
    service_tier: Optional[str] = None
    store: Optional[bool] = None
    temperature: Optional[float] = None
    text: Optional[Dict[str, Any]] = None
    tool_choice: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    top_logprobs: Optional[int] = None
    top_p: Optional[float] = None
    truncation: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    user: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None


# ---------- “Part” helpers ----------
class ReasoningSummaryPart(KWStruct):
    type: Literal["summary_text"]
    text: str


class OutputTextPart(KWStruct):
    type: Literal["output_text"]
    annotations: List[Any] = []
    logprobs: List[Any] = []
    text: str = ""


# ---------- Event variants ----------
class ResponseCreated(KWStruct, tag="response.created"):
    sequence_number: int
    response: ResponseCore


class ResponseInProgress(KWStruct, tag="response.in_progress"):
    sequence_number: int
    response: ResponseCore


class ResponseOutputItemAdded(KWStruct, tag="response.output_item.added"):
    sequence_number: int
    output_index: int
    item: Dict[str, Any]


class ResponseReasoningSummaryPartAdded(KWStruct, tag="response.reasoning_summary_part.added"):
    sequence_number: int
    item_id: str
    output_index: int
    summary_index: int
    part: ReasoningSummaryPart


class ResponseReasoningSummaryTextDelta(KWStruct, tag="response.reasoning_summary_text.delta"):
    sequence_number: int
    item_id: str
    output_index: int
    summary_index: int
    delta: str
    obfuscation: Optional[str] = None


class ResponseReasoningSummaryTextDone(KWStruct, tag="response.reasoning_summary_text.done"):
    sequence_number: int
    item_id: str
    output_index: int
    summary_index: int
    text: str


class ResponseReasoningSummaryPartDone(KWStruct, tag="response.reasoning_summary_part.done"):
    sequence_number: int
    item_id: str
    output_index: int
    summary_index: int
    part: ReasoningSummaryPart


class ResponseOutputItemDone(KWStruct, tag="response.output_item.done"):
    sequence_number: int
    output_index: int
    item: Dict[str, Any]


class ResponseContentPartAdded(KWStruct, tag="response.content_part.added"):
    sequence_number: int
    item_id: str
    output_index: int
    content_index: int
    part: OutputTextPart


class ResponseOutputTextDelta(KWStruct, tag="response.output_text.delta"):
    sequence_number: int
    item_id: str
    output_index: int
    content_index: int
    delta: str
    logprobs: List[Any] = []
    obfuscation: Optional[str] = None


class ResponseOutputTextDone(KWStruct, tag="response.output_text.done"):
    sequence_number: int
    item_id: str
    output_index: int
    content_index: int
    text: str
    logprobs: List[Any] = []


class ResponseContentPartDone(KWStruct, tag="response.content_part.done"):
    sequence_number: int
    item_id: str
    output_index: int
    content_index: int
    part: OutputTextPart


# NEW: function call argument streaming
class ResponseFunctionCallArgumentsDelta(KWStruct, tag="response.function_call_arguments.delta"):
    sequence_number: int
    item_id: str
    output_index: int
    delta: str
    obfuscation: Optional[str] = None


class ResponseFunctionCallArgumentsDone(KWStruct, tag="response.function_call_arguments.done"):
    sequence_number: int
    item_id: str
    output_index: int
    arguments: str


# NEW: custom tool call input streaming
class ResponseCustomToolCallInputDelta(KWStruct, tag="response.custom_tool_call_input.delta"):
    sequence_number: int
    item_id: str
    output_index: int
    delta: str
    obfuscation: Optional[str] = None


class ResponseCustomToolCallInputDone(KWStruct, tag="response.custom_tool_call_input.done"):
    sequence_number: int
    item_id: str
    output_index: int
    input: str


class ResponseCompleted(KWStruct, tag="response.completed"):
    sequence_number: int
    response: ResponseCore


# ---------- Catch-all ----------
class UnknownEvent(KWStruct):
    type: str
    __raw__: Dict[str, Any] = msgspec.field(default_factory=dict)


# ---------- The tagged union ----------
StreamEvent = (
    ResponseCreated
    | ResponseInProgress
    | ResponseOutputItemAdded
    | ResponseReasoningSummaryPartAdded
    | ResponseReasoningSummaryTextDelta
    | ResponseReasoningSummaryTextDone
    | ResponseReasoningSummaryPartDone
    | ResponseOutputItemDone
    | ResponseContentPartAdded
    | ResponseOutputTextDelta
    | ResponseOutputTextDone
    | ResponseContentPartDone
    | ResponseFunctionCallArgumentsDelta
    | ResponseFunctionCallArgumentsDone
    | ResponseCustomToolCallInputDelta
    | ResponseCustomToolCallInputDone
    | ResponseCompleted
)


# Normalized delta for callbacks
@dataclass
class Delta:
    kind: str
    output_index: Optional[int] = None
    item_id: Optional[str] = None
    content_index: Optional[int] = None
    summary_index: Optional[int] = None
    text: Optional[str] = None
    name: Optional[str] = None
    call_id: Optional[str] = None
    status: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedResponse:
    response_id: Optional[str] = None
    status: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None

    text: str = ""
    reasoning_summaries: List[str] = field(default_factory=list)
    function_calls: List[Dict[str, Any]] = field(default_factory=list)
    custom_tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    snapshot: Optional[ResponseCore] = None


AnyDeltaCallback = Union[Callable[[Delta], None], Callable[[Delta], Awaitable[None]]]


# Internal per-item state
@dataclass
class _MessageState:
    id: str
    output_index: int
    role: str = "assistant"
    parts: Dict[int, List[str]] = field(default_factory=dict)  # content_index -> chunks


@dataclass
class _ReasoningState:
    id: str
    output_index: int
    summaries: Dict[int, List[str]] = field(default_factory=dict)  # summary_index -> chunks


@dataclass
class _FunctionCallState:
    id: str
    output_index: int
    name: str
    call_id: str
    chunks: List[str] = field(default_factory=list)


@dataclass
class _CustomToolCallState:
    id: str
    output_index: int
    name: str
    call_id: str
    chunks: List[str] = field(default_factory=list)

