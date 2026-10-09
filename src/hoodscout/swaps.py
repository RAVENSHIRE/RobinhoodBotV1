"""Uniswap V3 Swap event decoding and strictly time-bounded launch activity metrics.

Every metric is computed only from swaps recorded at or before the observation cutoff.
Unknown inputs produce ``None`` rather than a neutral-looking zero.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .rpc import read_word, as_address

# keccak256('Swap(address,address,int256,int256,uint160,uint128,int24)')
SWAP_TOPIC = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'


def signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


@dataclass
class SwapEvent:
    pool: str
    block: int
    log_index: int
    transaction_hash: str
    timestamp: int
    sender: str      # pool caller, usually a router, not necessarily the trader
    recipient: str
    amount0: int     # pool perspective: positive = paid into the pool by the trader
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int


def decode_swap(log: dict, timestamp: int) -> SwapEvent:
    topics = log['topics']
    if len(topics) != 3 or topics[0].lower() != SWAP_TOPIC:
        raise ValueError('Not a V3 Swap event')
    data = log['data']
    return SwapEvent(
        pool=log['address'].lower(), block=int(log['blockNumber'], 16),
        log_index=int(log['logIndex'], 16), transaction_hash=log['transactionHash'].lower(),
        timestamp=timestamp, sender=as_address(int(topics[1], 16)),
        recipient=as_address(int(topics[2], 16)),
        amount0=signed(read_word(data, 0), 256), amount1=signed(read_word(data, 1), 256),
        sqrt_price_x96=read_word(data, 2) & ((1 << 160) - 1),
        liquidity=read_word(data, 3) & ((1 << 128) - 1),
        tick=signed(read_word(data, 4) & ((1 << 24) - 1), 24),
    )


def classify(swap: dict, weth_is_token0: bool) -> tuple[str | None, int]:
    """Return (side, signed WETH amount) from the trader's point of view of the token."""
    weth_raw = int(swap['amount0'] if weth_is_token0 else swap['amount1'])
    token_raw = int(swap['amount1'] if weth_is_token0 else swap['amount0'])
    if weth_raw > 0 or (weth_raw == 0 and token_raw < 0):
        return 'buy', weth_raw
    if weth_raw < 0 or (weth_raw == 0 and token_raw > 0):
        return 'sell', weth_raw
    return None, 0


def token_price_weth(sqrt_price_x96: int, weth_is_token0: bool, token_decimals: int | None) -> float | None:
    if token_decimals is None or sqrt_price_x96 <= 0:
        return None
    ratio = Decimal(sqrt_price_x96) ** 2 / Decimal(2 ** 192)  # raw token1 per raw token0
    if weth_is_token0:
        price = Decimal(1) / (ratio * Decimal(10) ** (18 - token_decimals))
    else:
        price = ratio * Decimal(10) ** (token_decimals - 18)
    return float(price) if price > 0 else None


def _round(value, digits=6):
    return None if value is None else round(value, digits)


def swap_metrics(swaps: list[dict], *, weth_is_token0: bool, token_decimals: int | None,
                 as_of: float, eth_usd: float | None = None, history_complete: bool = False) -> dict[str, Any]:
    """Aggregate recorded swaps with ``timestamp <= as_of``.

    ``tx_from`` is the transaction sender. Wallet-based metrics only count resolved
    senders and are withheld when an affected swap is still unresolved.
    """
    rows = sorted((s for s in swaps if s['timestamp'] <= as_of),
                  key=lambda s: (s['block'], s['log_index']))
    enriched = []
    for row in rows:
        side, weth_raw = classify(row, weth_is_token0)
        if side is None:
            continue
        enriched.append({**row, 'side': side, 'weth': abs(weth_raw) / 1e18,
                         'price': token_price_weth(int(row['sqrt_price_x96']), weth_is_token0, token_decimals)})
    buys = [s for s in enriched if s['side'] == 'buy']
    sells = [s for s in enriched if s['side'] == 'sell']
    resolved = [s for s in enriched if s.get('tx_from')]
    all_resolved = len(resolved) == len(enriched)

    def unique(items, side=None):
        return len({s['tx_from'] for s in items if s.get('tx_from') and (side is None or s['side'] == side)})

    def window(start, end):
        return [s for s in enriched if start < s['timestamp'] <= end]

    last30, prev30, last60 = window(as_of - 30, as_of), window(as_of - 60, as_of - 30), window(as_of - 60, as_of)
    windows_resolved = all(s.get('tx_from') for s in last60)
    buy_volume = sum(s['weth'] for s in buys)
    sell_volume = sum(s['weth'] for s in sells)

    by_buyer: dict[str, float] = {}
    for s in buys:
        if s.get('tx_from'):
            by_buyer[s['tx_from']] = by_buyer.get(s['tx_from'], 0.0) + s['weth']
    ranked = sorted(by_buyer.values(), reverse=True)
    concentration_known = history_complete and all(s.get('tx_from') for s in buys) and buy_volume > 0

    buyers, sellers = {s['tx_from'] for s in buys if s.get('tx_from')}, {s['tx_from'] for s in sells if s.get('tx_from')}
    round_trip = buyers & sellers
    round_trip_volume = sum(s['weth'] for s in enriched if s.get('tx_from') in round_trip)
    same_block = {(s['tx_from'], s['block']) for s in buys if s.get('tx_from')} & \
                 {(s['tx_from'], s['block']) for s in sells if s.get('tx_from')}

    per_second: dict[int, int] = {}
    for s in enriched:
        per_second[s['timestamp']] = per_second.get(s['timestamp'], 0) + 1

    # V3 prices move only through swaps: the 60s path starts at the last price before the window.
    price_range = None
    if history_complete and token_decimals is not None:
        before = [s['price'] for s in enriched if s['timestamp'] <= as_of - 60 and s['price']]
        path = before[-1:] + [s['price'] for s in last60 if s['price']]
        if len(path) >= 2:
            price_range = 100 * (max(path) / min(path) - 1)
        elif len(path) == 1:
            price_range = 0.0

    usd = eth_usd if isinstance(eth_usd, (int, float)) and eth_usd > 0 else None
    return {
        'swap_tracking': True,
        'swap_history_complete': history_complete,
        'activity_as_of': as_of,
        'swap_count': len(enriched), 'buy_count': len(buys), 'sell_count': len(sells),
        'buy_volume_weth': _round(buy_volume, 9), 'sell_volume_weth': _round(sell_volume, 9),
        'net_flow_weth': _round(buy_volume - sell_volume, 9),
        'buy_volume_usd': _round(buy_volume * usd, 2) if usd else None,
        'sell_volume_usd': _round(sell_volume * usd, 2) if usd else None,
        'trader_resolution_pct': _round(100 * len(resolved) / len(enriched), 1) if enriched else None,
        'unique_traders': unique(enriched), 'unique_buyers': unique(buys), 'unique_sellers': unique(sells),
        'swaps_last_30s': len(last30), 'buys_last_30s': sum(s['side'] == 'buy' for s in last30),
        'sells_last_30s': sum(s['side'] == 'sell' for s in last30),
        'swaps_per_minute': len(last60),
        'unique_buyers_last_30s': unique(last30, 'buy') if windows_resolved else None,
        'unique_buyers_prev_30s': unique(prev30, 'buy') if windows_resolved else None,
        'buyer_acceleration': (unique(last30, 'buy') - unique(prev30, 'buy')) if windows_resolved else None,
        'peak_swaps_per_second': max(per_second.values()) if per_second else 0,
        'top_buyer_share_pct': _round(100 * ranked[0] / buy_volume, 2) if concentration_known else None,
        'top3_buyer_share_pct': _round(100 * sum(ranked[:3]) / buy_volume, 2) if concentration_known else None,
        'round_trip_wallets': len(round_trip) if all_resolved else None,
        'round_trip_volume_share_pct': (_round(100 * round_trip_volume / (buy_volume + sell_volume), 2)
                                        if all_resolved and buy_volume + sell_volume > 0 else None),
        'same_block_round_trips': len(same_block) if all_resolved else None,
        'swap_price_range_pct_60s': _round(price_range, 4),
        'first_swap_at': enriched[0]['timestamp'] if enriched else None,
        'last_swap_age_seconds': _round(as_of - enriched[-1]['timestamp'], 3) if enriched else None,
        'holder_concentration': None,  # Requires Transfer indexing; not inferred from swaps.
    }
