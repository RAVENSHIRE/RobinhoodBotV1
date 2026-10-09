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
    cost_usd: float


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
    gross = exit_row['price_usd'] / entry['price_usd'] - 1
    return {'pool': pool, 'entry_second': entry_second, 'status': 'ok',
            **asdict(ReplayResult(pool, entry_second, entry['ts'], exit_row['ts'],
                entry_price, exit_price, ret * 100, notional_usd * ret,
                notional_usd * (gross - ret)))}


def _stats(trades: list[dict]) -> dict:
    returns = [r['return_pct'] for r in trades]
    pnls = [r['net_pnl_usd'] for r in trades]
    return {'trades': len(trades),
            'win_rate_pct': round(100 * sum(x > 0 for x in returns) / len(returns), 2) if returns else None,
            'expectancy_usd': round(sum(pnls) / len(pnls), 4) if pnls else None,
            'net_pnl_usd': round(sum(pnls), 2)}


def summarize_replay(results: list[dict], holdout_fraction: float = 0.3) -> dict:
    """All attempted launches are reported, including the ones excluded for missing data.

    Trades are ordered by entry time. The latest ``holdout_fraction`` is reported separately
    as a chronological holdout; no parameters are fitted, so this is a stability check only.
    """
    valid = sorted((r for r in results if r.get('status') == 'ok'), key=lambda r: r['entry_ts'])
    returns = [r['return_pct'] for r in valid]
    excluded: dict[str, int] = {}
    for r in results:
        if r.get('status') != 'ok':
            excluded[str(r.get('status'))] = excluded.get(str(r.get('status')), 0) + 1
    equity = peak = drawdown = 0.0
    for r in valid:
        equity += r['net_pnl_usd']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    split = len(valid) - int(len(valid) * holdout_fraction)
    return {'attempted': len(results), 'usable': len(valid), 'excluded': len(results) - len(valid),
            'excluded_by_reason': excluded,
            'wins': sum(x > 0 for x in returns),
            'win_rate_pct': round(100 * sum(x > 0 for x in returns) / len(returns), 2) if returns else None,
            'mean_return_pct': round(sum(returns) / len(returns), 2) if returns else None,
            'expectancy_usd': _stats(valid)['expectancy_usd'],
            'net_pnl_usd': round(sum(r['net_pnl_usd'] for r in valid), 2),
            'costs_usd': round(sum(r.get('cost_usd', 0) for r in valid), 2),
            'max_drawdown_usd': round(drawdown, 2),
            'in_sample': _stats(valid[:split]),
            'chronological_holdout': {**_stats(valid[split:]), 'fraction': holdout_fraction},
            'cost_model': 'fee+slippage haircut per side; excludes gas, MEV, latency and fillability'}
