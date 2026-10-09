"""Strictly historical replay/backtest: entry snapshots must precede exit snapshots."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ReplayResult:
    pool: str
    entry_second: int
    entry_ts: float
    exit_ts: float
    entry_price: float
    exit_price: float
    return_pct: float
    net_pnl_usd: float


def replay_pool(pool: str, launch_timestamp: float, snapshots: list[dict],
                entry_second: int, exit_after_seconds: int = 900,
                notional_usd: float = 25.0, cost_bps_per_side: float = 130.0,
                tolerance_seconds: int = 20) -> dict[str, Any]:
    if not 0 < entry_second <= 3600 or exit_after_seconds < 1:
        raise ValueError('Invalid replay horizon')
    ordered = sorted(snapshots, key=lambda s: s['ts'])
    entry_target = launch_timestamp + entry_second
    # Entry cannot occur before target time. Exit must occur later and after target hold duration.
    entries = [s for s in ordered if entry_target <= s['ts'] <= entry_target + tolerance_seconds
               and isinstance(s.get('price_usd'), (int, float)) and s['price_usd'] > 0
               and s.get('liquidity_verified') is True]
    if not entries:
        return {'pool': pool, 'entry_second': entry_second, 'status': 'insufficient_entry_data'}
    entry = entries[0]
    exit_target = entry['ts'] + exit_after_seconds
    exits = [s for s in ordered if exit_target <= s['ts'] <= exit_target + tolerance_seconds
             and isinstance(s.get('price_usd'), (int, float)) and s['price_usd'] > 0]
    if not exits:
        return {'pool': pool, 'entry_second': entry_second, 'status': 'insufficient_exit_data'}
    exit_row = exits[0]
    entry_price = entry['price_usd'] * (1 + cost_bps_per_side / 10000)
    exit_price = exit_row['price_usd'] * (1 - cost_bps_per_side / 10000)
    ret = exit_price / entry_price - 1
    return {'pool': pool, 'entry_second': entry_second, 'status': 'ok',
            **asdict(ReplayResult(pool, entry_second, entry['ts'], exit_row['ts'],
                entry_price, exit_price, ret * 100, notional_usd * ret))}


def summarize_replay(results: list[dict]) -> dict:
    valid = [r for r in results if r.get('status') == 'ok']
    returns = [r['return_pct'] for r in valid]
    return {'attempted': len(results), 'usable': len(valid), 'excluded': len(results) - len(valid),
            'wins': sum(x > 0 for x in returns),
            'win_rate_pct': round(100 * sum(x > 0 for x in returns) / len(returns), 2) if returns else None,
            'mean_return_pct': round(sum(returns) / len(returns), 2) if returns else None,
            'net_pnl_usd': round(sum(r['net_pnl_usd'] for r in valid), 2)}
