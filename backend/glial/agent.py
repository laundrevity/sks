# backend/glial/agent.py
from logging import getLogger as get_logger
from inspect import isawaitable
import logging
import os

from aiohttp import ClientSession

from glial.streaming import stream_response
from glial.tools.registry import gather_tools

_URL = "https://api.openai.com/v1/responses"
_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
}
_MODEL = "gpt-5"

logging.basicConfig(level=logging.INFO)

class Agent:
    def __init__(self, on_delta):
        self.log = get_logger(__name__)
        self.locals = locals()
        self.globals = globals()
        self.tool_schemas, self.tools = gather_tools(self)
        self.log.info("gathered tool_schemas[%s], tools[%s]", self.tool_schemas, self.tools)
        self.session = ClientSession()
        self.items = []  # running conversation items (JSON-serializable)
        self.on_delta = on_delta

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.__aexit__(exc_type, exc_val, exc_tb)

    async def __call__(self, prompt: str):
        """
        Returns:
          dict: { "total_tokens": int|None, "new_items": list[dict] }
        """
        # capture how many items existed before this turn
        start_len = len(self.items)
        # add the user message as an item
        self.items.append({"role": "user", "content": prompt})

        while True:
            payload = {
                "input": self.items,
                "model": _MODEL,
                "stream": True,
                "reasoning": {"effort": "medium", "summary": "auto"},
                "text": {"verbosity": "high"},
                "tools": self.tool_schemas,
            }

            async with self.session.post(_URL, json=payload, headers=_HEADERS) as resp:
                if resp.status >= 400:
                    try:
                        text = await resp.text()
                    except Exception as e:
                        text = f"<could not read body>: exc {e}"
                    self.log.error(
                        "Request failed: status[%s] body[%s] items_len[%s]",
                        resp.status, text, len(self.items)
                    )
                resp.raise_for_status()
                final = await stream_response(resp.content.iter_any(), on_delta=self.on_delta)
                self.log.debug("rcvd final[%s]", final)

            # append model output items into our running transcript
            for item in final.snapshot.output:
                self.items.append(item)

            # tool invocation loops
            if final.function_calls:
                for fc in final.function_calls:
                    func, kwargs = self.tools[fc["name"]], fc["arguments"]
                    res = func(**kwargs)
                    if isawaitable(res):
                        res = await res
                    self.items.append({
                        "type": "function_call_output",
                        "call_id": fc["call_id"],
                        "output": str(res),
                    })
            elif final.custom_tool_calls:
                for ctc in final.custom_tool_calls:
                    func, input_data = self.tools[ctc["name"]], ctc["input"]
                    res = func(input_data)
                    if isawaitable(res):
                        res = await res
                    self.items.append({
                        "type": "custom_tool_call_output",
                        "call_id": ctc["call_id"],
                        "output": str(res),
                    })
            else:
                # done for this round; compute delta items and return usage + new items
                total = None
                try:
                    total = final.usage.get("total_tokens")
                except Exception:
                    pass
                new_items = self.items[start_len:]  # includes the user message and all assistant items from this round
                return {"total_tokens": total, "new_items": new_items}

