"""Factory discovery and cautious ERC-20/Uniswap V3 read-only introspection."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from typing import Any
from .rpc import RPC, RpcError, read_word, as_address, address_word, read_string
from .swaps import SWAP_TOPIC, SwapEvent, decode_swap

# keccak256('PoolCreated(address,address,uint24,int24,address)')
POOL_CREATED_TOPIC = '0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118'
ZERO_ADDRESS = '0x' + '0' * 40
SELECTORS = {
    'total_supply': '0x18160ddd', 'decimals': '0x313ce567', 'symbol': '0x95d89b41',
    'owner': '0x8da5cb5b', 'balance_of': '0x70a08231', 'slot0': '0x3850c7bd',
    'liquidity': '0x1a686502', 'token0': '0x0dfe1681', 'token1': '0xd21220a7'
}


@dataclass
class PoolEvent:
    address: str
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    block: int
    timestamp: int
    transaction_hash: str
    log_index: int


def decode_pool_created(log: dict, timestamp: int) -> PoolEvent:
    topics = log['topics']
    if len(topics) != 4 or topics[0].lower() != POOL_CREATED_TOPIC:
        raise ValueError('Not a V3 PoolCreated event')
    fee = int(topics[3], 16)
    raw_tick = read_word(log['data'], 0) & ((1 << 24) - 1)
    tick = raw_tick - (1 << 24) if raw_tick & (1 << 23) else raw_tick
    return PoolEvent(
        address=as_address(read_word(log['data'], 1)),
        token0=as_address(int(topics[1], 16)),
        token1=as_address(int(topics[2], 16)),
        fee=fee, tick_spacing=tick, block=int(log['blockNumber'], 16),
        timestamp=timestamp, transaction_hash=log['transactionHash'],
        log_index=int(log['logIndex'], 16),
    )


def v3_token1_per_token0(sqrt_price_x96: int, decimals0: int, decimals1: int) -> Decimal | None:
    if sqrt_price_x96 <= 0:
        return None
    raw_ratio = Decimal(sqrt_price_x96) ** 2 / Decimal(2 ** 192)
    return raw_ratio * (Decimal(10) ** (decimals0 - decimals1))


def calculate_usd_state(
    token0: str, token1: str, weth: str, dec0: int, dec1: int,
    sqrt_price_x96: int, balance0: int, balance1: int, eth_usd: float | None,
) -> tuple[float | None, float | None]:
    """Token USD spot and *balance-based TVL estimate*, not executable liquidity.

    Only values WETH pairs, and only when an external ETH/USD reference is configured.
    """
    if eth_usd is None:
        return None, None
    ratio = v3_token1_per_token0(sqrt_price_x96, dec0, dec1)
    if ratio is None or ratio <= 0:
        return None, None
    weth_lc = weth.lower()
    reserve0 = Decimal(balance0) / Decimal(10 ** dec0)
    reserve1 = Decimal(balance1) / Decimal(10 ** dec1)
    usd = Decimal(str(eth_usd))
    if token0.lower() == weth_lc:
        token_usd = usd / ratio
        tvl = reserve0 * usd + reserve1 * token_usd
    elif token1.lower() == weth_lc:
        token_usd = usd * ratio
        tvl = reserve1 * usd + reserve0 * token_usd
    else:
        return None, None
    if tvl < 0 or token_usd <= 0:
        return None, None
    return float(token_usd), float(tvl)


class ChainReader:
    def __init__(self, rpc: RPC, factory: str, weth: str, eth_usd: float | None):
        self.rpc, self.factory, self.weth, self.eth_usd = rpc, factory, weth, eth_usd
        self.timestamps: dict[int, int] = {}

    def _timestamp(self, block: int) -> int:
        if block not in self.timestamps:
            self.timestamps[block] = self.rpc.block_timestamp(block)
            if len(self.timestamps) > 1000:
                self.timestamps.clear()
        return self.timestamps[block]

    def block_time(self, block: int) -> int:
        return self._timestamp(block)

    def _log_time(self, log: dict) -> int:
        # Newer Nitro/geth nodes include blockTimestamp; otherwise read the block header.
        stamp = log.get('blockTimestamp')
        if isinstance(stamp, str) and stamp.startswith('0x'):
            return int(stamp, 16)
        return self._timestamp(int(log['blockNumber'], 16))

    def discover(self, start: int, end: int) -> list[PoolEvent]:
        logs = self.rpc.logs(self.factory, POOL_CREATED_TOPIC, start, end)
        return [decode_pool_created(log, self._log_time(log)) for log in logs]

    def swaps(self, pools: list[str], start: int, end: int) -> list[SwapEvent]:
        """Swap logs for the watched pools in one bounded eth_getLogs request."""
        if not pools:
            return []
        wanted = {p.lower() for p in pools}
        logs = self.rpc.logs(sorted(wanted), SWAP_TOPIC, start, end)
        return [decode_swap(log, self._log_time(log)) for log in logs
                if str(log.get('address', '')).lower() in wanted and not log.get('removed')]

    def _safe_uint(self, token: str, selector: str) -> int | None:
        try:
            return read_word(self.rpc.eth_call(token, selector))
        except (RpcError, ValueError, TypeError):
            return None

    def _balance(self, token: str, holder: str) -> int | None:
        return self._safe_uint(token, SELECTORS['balance_of'] + address_word(holder))

    def token_state(self, token: str) -> dict[str, Any]:
        try:
            has_code = self.rpc.code(token)
        except RpcError:
            has_code = None
        supply = self._safe_uint(token, SELECTORS['total_supply'])
        decimals = self._safe_uint(token, SELECTORS['decimals'])
        if decimals is not None and decimals > 36:
            decimals = None
        owner_uint = self._safe_uint(token, SELECTORS['owner'])
        owner = as_address(owner_uint) if owner_uint is not None else None
        try:
            symbol = read_string(self.rpc.eth_call(token, SELECTORS['symbol']))
        except RpcError:
            symbol = None
        return {
            'token_address': token, 'contract_has_code': has_code,
            'total_supply': str(supply) if supply is not None else None,
            'decimals': decimals, 'symbol': symbol,
            'owner': owner, 'owner_renounced': owner == ZERO_ADDRESS if owner else None,
        }

    def pool_state(self, event: PoolEvent) -> dict[str, Any]:
        token = event.token1 if event.token0.lower() == self.weth.lower() else event.token0
        result = {
            **asdict(event), **self.token_state(token), 'pool_address': event.address,
            'quote_asset': self.weth,
            'price_usd': None, 'liquidity_usd': None, 'tvl_usd_estimate': None,
            'price_impact_pct': None, 'liquidity_verified': False,
            'liquidity_metric': 'balance_based_tvl_proxy_not_executable_depth',
        }
        result['token0_metadata'] = self.token_state(event.token0)
        result['token1_metadata'] = self.token_state(event.token1)
        try:
            sqrt_p = self._safe_uint(event.address, SELECTORS['slot0'])
            if sqrt_p is not None:
                result['sqrt_price_x96'] = str(sqrt_p)
                a, b = result['token0_metadata'], result['token1_metadata']
                if a['decimals'] is not None and b['decimals'] is not None:
                    bal0 = self._balance(event.token0, event.address)
                    bal1 = self._balance(event.token1, event.address)
                    if bal0 is not None and bal1 is not None:
                        price, tvl = calculate_usd_state(event.token0, event.token1, self.weth,
                            a['decimals'], b['decimals'], sqrt_p, bal0, bal1, self.eth_usd)
                        result['price_usd'], result['tvl_usd_estimate'] = price, tvl
                        # TVL is deliberately *not* used as executable liquidity.
        except (RpcError, ValueError, InvalidOperation, ZeroDivisionError, OverflowError):
            pass
        return result
