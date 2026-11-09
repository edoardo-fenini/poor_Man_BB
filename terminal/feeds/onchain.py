"""Large on-chain transfers: Bitcoin (blockchain.info stream) and Ethereum (Etherscan polling)."""
import asyncio
import csv
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import websockets

from terminal.bus import Bus
from terminal.events import WhaleEvent
from terminal.feeds.base import safe
from terminal.prices import Prices

ADDRESSES_FILE = Path(__file__).parent.parent / "data" / "addresses.csv"
ETHERSCAN = "https://api.etherscan.io/v2/api"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKENS = {"USDT": ("0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
          "USDC": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6)}


def load_labels() -> dict[str, str]:
    """address (lowercase) -> label, e.g. an exchange name. Ships empty; add rows you trust."""
    with open(ADDRESSES_FILE, newline="") as fh:
        return {row["address"].lower(): row["label"] for row in csv.DictReader(fh) if row.get("address")}


def _who(address: str | None, labels: dict[str, str]) -> str:
    if not address:
        return "unknown"
    return labels.get(address.lower()) or (f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address)


def _amount(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:,.2f}"


def parse_btc_tx(msg: dict, btc_usd: float, min_usd: float, labels: dict[str, str]) -> WhaleEvent | None:
    if msg["op"] != "utx":
        return None
    tx = msg["x"]
    senders = {i["prev_out"].get("addr") for i in tx["inputs"]}
    # outputs going back to a sender are change, not a transfer
    moved = [o for o in tx["out"] if o.get("addr") not in senders]
    btc = sum(o["value"] for o in moved) / 1e8
    usd = btc * btc_usd
    if usd < min_usd or not moved:
        return None
    biggest = max(moved, key=lambda o: o["value"])
    sender = next(iter(senders - {None}), None)
    return WhaleEvent(
        id=tx["hash"], type="onchain", symbol="BTC-USD", usd=usd, side="",
        detail=f"{_amount(btc)} BTC {_who(sender, labels)} → {_who(biggest.get('addr'), labels)}",
        url=f"https://mempool.space/tx/{tx['hash']}", ts=float(tx["time"]),
    )


def parse_eth_block(block: dict, eth_usd: float, min_usd: float, labels: dict[str, str]) -> list[WhaleEvent]:
    result = block["result"]
    ts = float(int(result["timestamp"], 16))
    out = []
    for tx in result["transactions"]:
        try:
            eth = int(tx["value"], 16) / 1e18
        except (KeyError, ValueError, TypeError):
            continue
        if eth * eth_usd >= min_usd:
            out.append(WhaleEvent(
                id=tx["hash"], type="onchain", symbol="ETH-USD", usd=eth * eth_usd, side="",
                detail=f"{_amount(eth)} ETH {_who(tx.get('from'), labels)} → {_who(tx.get('to'), labels)}",
                url=f"https://etherscan.io/tx/{tx['hash']}", ts=ts))
    return out


def parse_token_logs(payload: dict, token: str, decimals: int, min_usd: float,
                     labels: dict[str, str]) -> list[WhaleEvent]:
    """ERC-20 Transfer logs for a dollar stablecoin: 1 token = 1 USD."""
    logs = payload["result"]
    if not isinstance(logs, list):
        raise ValueError(str(logs)[:100])  # Etherscan reports errors as a string here
    out = []
    for log in logs:
        try:
            amount = int(log["data"], 16) / 10**decimals
            sender, receiver = ("0x" + topic[-40:] for topic in log["topics"][1:3])
            tx, ts = log["transactionHash"], float(int(log["timeStamp"], 16))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if amount >= min_usd:
            out.append(WhaleEvent(
                id=f"{tx}:{log.get('logIndex', '')}", type="onchain", symbol=f"{token}-USD", usd=amount, side="",
                detail=f"{_amount(amount)} {token} {_who(sender, labels)} → {_who(receiver, labels)}",
                url=f"https://etherscan.io/tx/{tx}", ts=ts))
    return out


async def btc_stream(bus: Bus, prices: Prices, settings: Callable[[], dict]) -> None:
    labels = load_labels()
    async with websockets.connect("wss://ws.blockchain.info/inv", open_timeout=10, ping_interval=20) as ws:
        await ws.send(json.dumps({"op": "unconfirmed_sub"}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), 120)  # the mempool is never quiet for two minutes
            price = prices.last("BTC-USD")
            if price is None:
                continue  # no price yet: a dollar value would be a guess
            for whale in safe(lambda r: parse_btc_tx(json.loads(r), price, settings()["whale_onchain_usd"], labels),
                              raw, "btc-onchain"):
                bus.publish(whale)


async def eth_poll(bus: Bus, key: str, prices: Prices, settings: Callable[[], dict], every: float = 12.0) -> None:
    labels = load_labels()
    last_block = None
    async with httpx.AsyncClient(timeout=20) as client:
        async def call(**params) -> dict:
            response = await client.get(ETHERSCAN, params={"chainid": 1, "apikey": key, **params})
            response.raise_for_status()
            return response.json()

        while True:
            head = int((await call(module="proxy", action="eth_blockNumber"))["result"], 16)
            first = head if last_block is None else max(last_block + 1, head - 5)  # never fall far behind
            price, floor = prices.last("ETH-USD"), settings()["whale_onchain_usd"]
            for number in range(first, head + 1):
                if price is not None:
                    block = await call(module="proxy", action="eth_getBlockByNumber", tag=hex(number), boolean="true")
                    for whale in safe(lambda b: parse_eth_block(b, price, floor, labels), block, "eth-onchain"):
                        bus.publish(whale)
                for token, (address, decimals) in TOKENS.items():
                    logs = await call(module="logs", action="getLogs", fromBlock=number, toBlock=number,
                                      address=address, topic0=TRANSFER)
                    for whale in safe(lambda p: parse_token_logs(p, token, decimals, floor, labels), logs, "eth-onchain"):
                        bus.publish(whale)
                    await asyncio.sleep(0.25)  # free tier: 5 calls per second
            last_block = head
            await asyncio.sleep(every)
