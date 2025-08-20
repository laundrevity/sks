from decimal import Decimal as deci

from aiohttp import ClientSession

from glial.tools.registry import tool


@tool("Get the current price of a spot pair from Coinbase", pair="Hyphen-separated pair of spot instruments, e.g. BTC-USD")
async def get_spot_pair_price(pair: str):
    async with ClientSession() as session:
        async with session.get(f"https://api.exchange.coinbase.com/products/{pair}/book") as resp:
            resp.raise_for_status()
            resp_json = await resp.json()
            bid, ask = deci(resp_json["bids"][0][0]), deci(resp_json["asks"][0][0])
            return (bid + ask) / deci(2)

