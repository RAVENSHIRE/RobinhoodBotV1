"""Conservative quote-based paper executor. Live sending is deliberately unavailable."""
from __future__ import annotations
import time
from .store import Store


class LiveExecutionDisabled(RuntimeError):
    pass


class LiveExecutor:
    """Separate interface; never silently falls through to wallet signing."""
    def execute_buy(self, state: dict):
        raise LiveExecutionDisabled(
            'Live execution requires an independently audited signer/router integration; '
            'this research release cannot broadcast transactions, even with LIVE_MODE=true.'
        )


class PaperExecutor:
    def __init__(self, store: Store, order_usd=25.0, slippage_bps=100.0,
                 fee_bps=30.0, stop_loss_pct=15.0, take_profit_pct=50.0,
                 max_hold_seconds=900, max_open_positions=3, max_exposure_usd=100, max_daily_loss_usd=75):
        self.store, self.order_usd, self.slippage_bps, self.fee_bps = store, order_usd, slippage_bps, fee_bps
        self.stop_loss_pct, self.take_profit_pct, self.max_hold_seconds = stop_loss_pct, take_profit_pct, max_hold_seconds
        self.max_open_positions, self.max_exposure_usd, self.max_daily_loss_usd = max_open_positions, max_exposure_usd, max_daily_loss_usd

    def buy(self, state: dict) -> dict:
        pool = state['pool_address']
        if self.store.has_position(pool):
            return {'action': 'skip', 'reason': 'duplicate_position'}
        current = self.store.open_positions()
        if len(current) >= self.max_open_positions:
            return {'action': 'skip', 'reason': 'max_positions'}
        if sum(p['size_usd'] for p in current) + self.order_usd > self.max_exposure_usd:
            return {'action': 'skip', 'reason': 'max_exposure'}
        if self.store.today_realized_pnl() <= -self.max_daily_loss_usd:
            return {'action': 'skip', 'reason': 'daily_loss_lock'}
        quote_out = state.get('quote_tokens_out')
        if not isinstance(quote_out, (int, float)) or quote_out <= 0:
            return {'action': 'skip', 'reason': 'missing_quote'}
        # Quoter already includes the pool fee. Paper fee/slippage are explicit ADDITIONAL haircuts.
        units = quote_out * (1 - (self.slippage_bps + self.fee_bps) / 10000)
        if units <= 0:
            return {'action': 'skip', 'reason': 'invalid_quote_haircut'}
        entry_price = self.order_usd / units
        self.store.open_position(pool, state['token_address'], self.order_usd, units, entry_price)
        return {'action': 'buy', 'units': units, 'entry_price': entry_price, 'cost_usd': self.order_usd, 'mode': 'PAPER'}

    def mark(self, state: dict, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        price = state.get('price_usd')
        if not isinstance(price, (int, float)) or price <= 0:
            return []
        outcomes = []
        for p in self.store.open_positions():
            if p['pool'] != state['pool_address'].lower():
                continue
            exit_price = price * (1 - (self.slippage_bps + self.fee_bps) / 10000)
            value = p['units'] * exit_price
            pnl = value - p['size_usd']
            ret = 100 * pnl / p['size_usd']
            age = now - p['opened']
            reason = ('STOP_LOSS' if ret <= -self.stop_loss_pct else
                      'TAKE_PROFIT' if ret >= self.take_profit_pct else
                      'MAX_HOLD' if age >= self.max_hold_seconds else None)
            if reason:
                self.store.close_position(p['id'], exit_price, pnl, reason, now)
                outcomes.append({'pool': p['pool'], 'reason': reason, 'pnl_usd': pnl, 'exit_price': exit_price})
        return outcomes
