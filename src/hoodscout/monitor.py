"""Robinhood PoolCreated scanner and early token observation loop."""
from __future__ import annotations
from dataclasses import asdict
import logging
import threading
import time

from .chain import ChainReader, PoolEvent
from .config import Config
from .decision_engine import DecisionEngine, hold
from .execution import PaperExecutor
from .gates import evaluate, deterministic_reasons, RiskLimits
from .quote import quote_buy
from .pricing import EthUsdFeed
from .rpc import RPC, RpcError
from .store import Store
from .swaps import swap_metrics

log = logging.getLogger(__name__)


class Monitor:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.rpc = RPC(cfg.rpc_url)
        # A legacy static ETH_USD value must never authorize an entry.
        self.reader = ChainReader(self.rpc, cfg.factory, cfg.weth, None)
        self.price_feed = EthUsdFeed(enabled=cfg.price_feed_mode != 'off',
            refresh_seconds=cfg.price_refresh_seconds, max_age_seconds=cfg.price_max_age_seconds,
            max_divergence_pct=cfg.price_max_divergence_pct,
            future_tolerance_seconds=cfg.price_future_tolerance_seconds)
        self.engine = DecisionEngine(cfg.decision_provider, cfg.typesafe_api_key, cfg.jev_endpoint,
                                     cfg.jev_model, timeout=cfg.jev_timeout_seconds)
        self.executor = PaperExecutor(store, cfg.paper_order_usd, cfg.paper_slippage_bps,
                                      cfg.paper_fee_bps, cfg.paper_stop_loss_pct, cfg.paper_take_profit_pct,
                                      cfg.paper_max_hold_seconds, cfg.max_open_positions,
                                      cfg.max_paper_exposure_usd, cfg.max_daily_loss_usd)
        self.limits = RiskLimits(cfg.min_liquidity_usd, cfg.min_volatility_pct, cfg.max_volatility_pct, cfg.min_confidence,
                                 cfg.max_risk_score, cfg.max_price_impact_pct, cfg.owner_renounced_required,
                                 cfg.price_max_age_seconds, cfg.quote_max_age_seconds,
                                 cfg.price_future_tolerance_seconds, cfg.min_unique_buyers,
                                 cfg.max_top_buyer_share_pct, cfg.activity_max_lag_seconds)
        self.active: dict[str, PoolEvent] = {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_error = None
        self.ready = False
        self._cursor = None
        self._quoter_verified = None
        # Last block whose swaps are fully recorded, and when that was known to be complete.
        self._synced: dict | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name='robinhood-scanner', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
        self.rpc.close()
        self.price_feed.close()

    def restore_active(self):
        now = time.time()
        open_pools = {p['pool'] for p in self.store.open_positions()}
        for item in self.store.pools(limit=100):
            if now - item['timestamp'] < self.cfg.monitor_minutes * 60 or item['address'].lower() in open_pools:
                event = PoolEvent(**item)
                self.active[event.address.lower()] = event

    def run(self):
        try:
            found_id = self.rpc.chain_id()
            if found_id != self.cfg.chain_id:
                raise RpcError(f'CHAIN ID MISMATCH: RPC returned {found_id}, expected {self.cfg.chain_id}')
            if not self.rpc.code(self.cfg.factory) or not self.rpc.code(self.cfg.weth):
                raise RpcError('Factory or WETH code missing on configured RPC')
            self.restore_active()
            if self.cfg.decision_provider == 'jev':
                # A labeled synthetic inference checks credentials/schema, never execution.
                self.engine.check_connection()
            self.ready = True
            while not self.stop_event.is_set():
                try:
                    self.price_feed.refresh()
                    self.scan_once()
                    self.resolve_traders()
                    self.update_active()
                    self.last_error = None
                    self.store.set_meta('scanner_last_error', '')
                    self.store.set_meta('scanner_last_ok', str(time.time()))
                except Exception as exc:
                    self.last_error = f'{type(exc).__name__}: {exc}'[:500]
                    self.store.set_meta('scanner_last_error', self.last_error)
                    log.exception('Scanner cycle failed')
                self.stop_event.wait(self.cfg.poll_interval)
        except Exception as exc:
            self.last_error = f'{type(exc).__name__}: {exc}'[:500]
            self.store.set_meta('scanner_last_error', self.last_error)
            log.exception('Scanner failed to start')
        finally:
            self.ready = False

    @staticmethod
    def _coverage_key(pool: str) -> str:
        return f'swap_coverage_from:{pool.lower()}'

    def scan_once(self):
        checked_at = time.time()
        tip = self.rpc.block_number()
        if self._cursor is None:
            # Versioned cursor: the old topic never found genuine PoolCreated events.
            # Preserve the DB and start one bounded BACKFILL_BLOCKS window on migration.
            prior = self.store.get_meta('pool_created_cursor_v2')
            self._cursor = int(prior) if prior else max(0, tip - self.cfg.backfill_blocks)
        # Bounded catchup. Only advance the cursor after the full chunk was read.
        for _ in range(3):
            if self._cursor >= tip:
                break
            end = min(tip, self._cursor + self.cfg.max_block_range)
            for event in self.reader.discover(self._cursor + 1, end):
                self.store.put_pool(asdict(event))
                if (event.address.lower() not in self.active and
                        len(self.active) < self.cfg.max_active_pools):
                    self.active[event.address.lower()] = event
                    # Swaps for this chunk are read below, so history starts at creation.
                    if not self.store.get_meta(self._coverage_key(event.address)):
                        self.store.set_meta(self._coverage_key(event.address), str(event.block))
            self._track_swaps(self._cursor + 1, end)
            self._cursor = end
            self.store.set_meta('pool_created_cursor_v2', str(self._cursor))
            self.store.set_meta('last_scanned_block', str(self._cursor))
        # Arbitrum-style chains create no empty blocks, so a caught-up cursor is complete
        # as of the tip check; while catching up it is complete as of the cursor block.
        synced_at = checked_at if self._cursor >= tip else self.reader.block_time(self._cursor)
        self._synced = {'block': self._cursor, 'at': synced_at}
        self.store.set_meta('swap_synced_block', str(self._cursor))
        self.store.set_meta('swap_synced_at', str(synced_at))

    def _track_swaps(self, start: int, end: int):
        pools = list(self.active)
        for pool in pools:
            if not self.store.get_meta(self._coverage_key(pool)):
                self.store.set_meta(self._coverage_key(pool), str(start))
        self.store.put_swaps(self.reader.swaps(pools, start, end))

    def resolve_traders(self):
        """Look up the transaction sender (EOA) for recorded swaps, bounded per cycle."""
        for tx_hash in self.store.unresolved_transactions(self.cfg.max_tx_lookups_per_cycle):
            try:
                sender = self.rpc.transaction_sender(tx_hash)
            except RpcError as exc:
                log.warning('Transaction sender lookup paused: %s', exc)
                return
            # '' marks an unavailable sender: never counted as a wallet, never retried forever.
            self.store.set_transaction_sender(tx_hash, sender or '')

    def _verify_quoter(self) -> bool:
        if not self.cfg.quoter:
            return False
        if self._quoter_verified is not None:
            if not self._quoter_verified:
                raise RpcError('Configured quoter points to a different factory or WETH')
            return self._quoter_verified
        if not self.rpc.code(self.cfg.quoter):
            raise RpcError('Configured quoter has no bytecode')
        from .rpc import as_address, read_word
        claimed_factory = as_address(read_word(self.rpc.eth_call(self.cfg.quoter, '0xc45a0155')))
        claimed_weth = as_address(read_word(self.rpc.eth_call(self.cfg.quoter, '0x4aa4a4fc')))
        self._quoter_verified = (claimed_factory.lower() == self.cfg.factory.lower() and
                                 claimed_weth.lower() == self.cfg.weth.lower())
        if not self._quoter_verified:
            raise RpcError('Configured quoter points to a different factory or WETH')
        return True

    def _state(self, event: PoolEvent) -> dict:
        reference = self.price_feed.refresh()
        self.reader.eth_usd = reference['price_usd'] if reference['verified'] else None
        state = self.reader.pool_state(event)
        now = time.time()
        state['observed_at'] = now
        state['age_seconds'] = max(0, now - event.timestamp)
        state['chain_id'] = self.cfg.chain_id
        state['eth_usd_feed'] = reference
        state['eth_usd_reference'] = reference['price_usd']
        state['eth_usd_timestamp'] = reference['timestamp']
        state['eth_usd_verified'] = reference['verified']
        prior = self.store.snapshots(event.address, limit=500)
        # Use only previously observed prices; never infer past 30s observations.
        past_prices = [s['price_usd'] for s in prior if
                       isinstance(s.get('price_usd'), (float, int)) and s['price_usd'] > 0 and now - s['ts'] <= 60]
        state['observed_price_change_pct'] = (100 * (state['price_usd'] / past_prices[0] - 1)
            if past_prices and state['price_usd'] is not None else None)
        state['volatility_pct'] = (100 * ((max(past_prices + [state['price_usd']]) /
            min(past_prices + [state['price_usd']])) - 1)
            if past_prices and state['price_usd'] is not None else None)
        state['volatility_source'] = 'snapshots_60s' if state['volatility_pct'] is not None else None
        state.update(self._activity(event, state, now))
        if state.get('swap_price_range_pct_60s') is not None:
            state['volatility_pct'] = state['swap_price_range_pct_60s']
            state['volatility_source'] = 'swaps_60s'
        if (self.cfg.quoter and reference['verified'] and state.get('sqrt_price_x96')
            and state.get('tvl_usd_estimate') is not None):
            try:
                self._verify_quoter()
                dec0 = state['token0_metadata']['decimals']
                dec1 = state['token1_metadata']['decimals']
                if dec0 is not None and dec1 is not None:
                    quote = quote_buy(self.rpc, self.cfg.quoter, event, self.cfg.weth,
                        reference['price_usd'], self.cfg.paper_order_usd, int(state['sqrt_price_x96']), dec0, dec1)
                    state.update(quote)
                    state['liquidity_verified'] = True  # Quote works; TVL is still approximate.
                    state['liquidity_usd'] = state['tvl_usd_estimate']
            except (RpcError, ValueError, ArithmeticError) as exc:
                state['quote_error'] = str(exc)[:160]
        # Long RPC calls may outlive the source freshness window. Do not mark paper
        # positions using a now-stale USD reference; gates recheck after model latency.
        if (not reference['verified'] or not
                -self.cfg.price_future_tolerance_seconds <= time.time() - reference['timestamp']
                <= self.cfg.price_max_age_seconds):
            state.update(eth_usd_verified=False, price_usd=None, liquidity_verified=False,
                         liquidity_usd=None, tvl_usd_estimate=None)
        return state

    def _activity(self, event: PoolEvent, state: dict, now: float) -> dict:
        unknown = {'swap_tracking': False, 'activity_lag_seconds': None, 'buy_count': None,
                   'sell_count': None, 'unique_buyers': None, 'top_buyer_share_pct': None}
        weth = self.cfg.weth.lower()
        if self._synced is None or weth not in (event.token0.lower(), event.token1.lower()):
            return unknown
        coverage = self.store.get_meta(self._coverage_key(event.address))
        rows = self.store.swaps(event.address, max_block=self._synced['block'])
        reference = state.get('eth_usd_reference') if state.get('eth_usd_verified') else None
        metrics = swap_metrics(rows, weth_is_token0=event.token0.lower() == weth,
                               token_decimals=state.get('decimals'), as_of=now, eth_usd=reference,
                               history_complete=coverage is not None and int(coverage) <= event.block)
        metrics['activity_lag_seconds'] = round(now - self._synced['at'], 3)
        metrics['swap_synced_block'] = self._synced['block']
        return metrics

    def _decide(self, state: dict) -> tuple[dict, dict]:
        if self.store.kill_switch():
            return hold('kill_switch'), {'provider': self.cfg.decision_provider, 'sent': False,
                                         'reason': 'kill_switch'}
        if self.cfg.decision_provider == 'jev' and self.cfg.jev_call_policy == 'prefiltered':
            # Each JEV call may cost credits. If the application gates already reject the
            # strongest possible model answer, the model cannot change the outcome.
            blocked = deterministic_reasons(state, self.limits)
            if blocked:
                return ({'action': 'skip', 'confidence': 0.0, 'risk_score': 10.0,
                         'error': 'not_sent_deterministic_gates_failed'},
                        {'provider': 'jev', 'sent': False, 'reason': 'deterministic_gates_failed',
                         'prefilter_reasons': blocked})
        return self.engine.decide(state)

    def update_active(self):
        now = time.time()
        current_positions = {p['pool'] for p in self.store.open_positions()}
        for pool, event in list(self.active.items()):
            age = now - event.timestamp
            if age > self.cfg.monitor_minutes * 60 and pool not in current_positions:
                self.active.pop(pool, None)
                # Swaps stop being recorded, so any later history would have gaps.
                self.store.delete_meta(self._coverage_key(pool))
                continue
            try:
                state = self._state(event)
                self.store.snapshot(pool, state)
                self.executor.mark(state)
                # During first two minutes, reconsider with fresh data; never auto-enter afterward.
                if age <= 120 and not self.store.has_position(pool):
                    # One decision per 30-second bucket, including the first sighting.
                    bucket = min(4, int(max(0, age) // 30))
                    bucket_key = f'decision_bucket:{pool}'
                    if self.store.get_meta(bucket_key) == str(bucket):
                        continue
                    self.store.set_meta(bucket_key, str(bucket))
                    normalized, raw = self._decide(state)
                    gates = evaluate(state, normalized, self.limits)
                    if self.store.kill_switch():
                        gates.update(approved=False, action='skip',
                                     reasons=gates['reasons'] + ['kill_switch'])
                    if gates['approved']:
                        order = self.executor.buy(state)
                        gates['execution'] = order
                        if order['action'] != 'buy':
                            gates['approved'], gates['action'] = False, 'skip'
                    self.store.put_decision(pool, state, normalized, raw, gates)
            except Exception as exc:
                log.exception('Unable to update pool %s', pool)
                self.store.set_meta('pool_error', f'{pool}: {exc}'[:450])
