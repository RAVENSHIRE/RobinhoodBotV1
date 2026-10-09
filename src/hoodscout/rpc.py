"""Minimal read-only EVM JSON-RPC client. Avoids a signing dependency."""
from __future__ import annotations
import itertools
import time
from typing import Any
import httpx


class RpcError(RuntimeError):
    pass


class RPC:
    def __init__(self, url: str, timeout: float = 12.0):
        self.url = url
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "HoodScout/0.1"})
        self.ids = itertools.count(1)

    def close(self):
        self.client.close()

    def call(self, method: str, params: list | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params or []}
        last_error = None
        for attempt in range(3):
            try:
                res = self.client.post(self.url, json=payload)
                if res.status_code in (429, 502, 503, 504):
                    raise RpcError(f"RPC HTTP {res.status_code}")
                res.raise_for_status()
                data = res.json()
                if "error" in data:
                    raise RpcError(f"{method}: {data['error']}")
                if "result" not in data:
                    raise RpcError(f"{method}: missing result")
                return data["result"]
            except (httpx.HTTPError, ValueError, RpcError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.35 * (2 ** attempt))
        raise RpcError(str(last_error))

    def chain_id(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def block_timestamp(self, number: int) -> int:
        result = self.call("eth_getBlockByNumber", [hex(number), False])
        if result is None:
            raise RpcError("Missing block")
        return int(result["timestamp"], 16)

    def logs(self, address: str | list[str], topic: str, start: int, end: int) -> list[dict]:
        return self.call("eth_getLogs", [{"address": address, "topics": [topic], "fromBlock": hex(start), "toBlock": hex(end)}])

    def transaction_sender(self, tx_hash: str) -> str | None:
        result = self.call("eth_getTransactionByHash", [tx_hash])
        sender = result.get("from") if isinstance(result, dict) else None
        if not isinstance(sender, str) or len(sender) != 42:
            return None
        return sender.lower()

    def code(self, address: str) -> bool:
        value = self.call("eth_getCode", [address, "latest"])
        return value not in ("0x", "0x0", None)

    def eth_call(self, contract: str, calldata: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": contract, "data": calldata}, block])


def address_word(value: str) -> str:
    address = value.removeprefix("0x")
    if len(address) != 40 or not all(x in '0123456789abcdefABCDEF' for x in address):
        raise ValueError("Expected 20-byte EVM address")
    return address.lower().rjust(64, "0")


def read_word(data: str, index: int = 0) -> int:
    stripped = data.removeprefix("0x")
    word = stripped[index * 64:(index + 1) * 64]
    if len(word) < 64:
        raise ValueError("Truncated ABI result")
    return int(word, 16)


def as_address(value: int) -> str:
    return "0x" + format(value & ((1 << 160) - 1), "040x")


def read_string(data: str) -> str | None:
    """Decode common ERC-20 dynamic string and bytes32 fallbacks."""
    try:
        raw = bytes.fromhex(data.removeprefix("0x"))
        if len(raw) == 32 and int.from_bytes(raw, "big") != 32:
            return raw.rstrip(b"\x00").decode("utf-8", errors="replace")[:96]
        offset = int.from_bytes(raw[:32], "big")
        if offset + 32 > len(raw):
            return None
        length = int.from_bytes(raw[offset:offset + 32], "big")
        if length > 512 or offset + 32 + length > len(raw):
            return None
        return raw[offset + 32:offset + 32 + length].decode("utf-8", errors="replace")[:96]
    except (ValueError, IndexError):
        return None
