from logging import getLogger as get_logger
from inspect import isawaitable
import logging
import os

from aiohttp import ClientSession

from glial.streaming import stream_response, Delta, AnyDeltaCallback
from glial.tools.registry import gather_tools


_URL = "https://api.openai.com/v1/responses"
_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv("OPENAI_API_KEY")}"}
_MODEL = "gpt-5"


logging.basicConfig(level=logging.INFO)

class Agent:
    def __init__(self, on_delta: AnyDeltaCallback):
        self.log = get_logger(f"{__name__}")
        self.locals = locals()
        self.globals = globals()
        self.tool_schemas, self.tools = gather_tools(self)
        self.log.info("gathered tool_schemas[%s], tools[%s]", self.tool_schemas, self.tools)
        self.log.info("set locals[%s], globals[%s]", self.locals, self.globals)
        self.session = ClientSession()
        self.items = []
        self.on_delta = on_delta

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.__aexit__(exc_type, exc_val, exc_tb)

    async def __call__(self, prompt: str):
        self.items.append({"role": "user", "content": prompt})

        while True:
            payload = {
                "input": self.items,
                "model": _MODEL,
                "stream": True,
                "reasoning": {
                    "effort": "medium",
                    "summary": "auto"
                },
                "text": {
                    "verbosity": "high"
                },
                "tools": self.tool_schemas
            }

            async with self.session.post(_URL, json=payload, headers=_HEADERS) as resp:
                if resp.status >= 400:
                    try:
                        text = await resp.text()
                    except Exception as e:
                        text = f"<could not read body>: exc{e}"
                    self.log.error("Request failed: status[%s] body[%s] self.input[%s]",
                                   resp.status, text, self.items)

                resp.raise_for_status()
                final = await stream_response(resp.content.iter_any(), on_delta=self.on_delta)
                self.log.debug("rcvd final[%s]", final)

            for item in final.snapshot.output:
                self.items.append(item)

            if final.function_calls:
                for fc in final.function_calls:
                    func, kwargs = self.tools[fc["name"]], fc["arguments"]
                    res = func(**kwargs)
                    if isawaitable(res):
                        res = await res
                    self.items.append({
                        "type": "function_call_output",
                        "call_id": fc["call_id"],
                        "output": str(res)
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
                        "output": str(res)
                    })
            else:
                return final.usage.get("total_tokens", None)

