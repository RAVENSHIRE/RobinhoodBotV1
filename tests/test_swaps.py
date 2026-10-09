"""Synthetic Swap-event fixtures (hand-encoded ABI data, not captured chain history)."""
from dataclasses import replace
import time
from unittest.mock import Mock

import pytest

from hoodscout.chain import ChainReader, PoolEvent, v3_token1_per_token0
from hoodscout.config import load_config
from hoodscout.execution import PaperExecutor
from hoodscout.monitor import Monitor
from hoodscout.rpc import RpcError
from hoodscout.store import Store
from hoodscout.swaps import SWAP_TOPIC, SwapEvent, decode_swap, swap_metrics, token_price_weth

WETH = '0x0bd7d308f8e1639fab988df18a8011f41eacad73'
TOKEN = '0x' + '11' * 20
POOL = '0x' + '22' * 20
ROUTER = '0x' + '33' * 20
Q96 = 2 ** 96


def word(value, bits=256):
    return format(value % (1 << bits), '064x')


def swap_log(amount0, amount1, sqrt_price=Q96, block=10, index=0, tx='aa', pool=POOL, **extra):
    data = '0x' + word(amount0) + word(amount1) + word(sqrt_price) + word(10 ** 18) + word(-60)
    return {'address': pool, 'topics': [SWAP_TOPIC, '0x' + ROUTER[2:].rjust(64, '0'),
            '0x' + ('44' * 20).rjust(64, '0')], 'data': data, 'blockNumber': hex(block),
            'logIndex': hex(index), 'transactionHash': '0x' + tx * 32, **extra}


def row(side, weth, ts, wallet, block=None, index=0, sqrt_price=Q96, weth_is_token0=True):
    weth_raw = int(weth * 10 ** 18) * (1 if side == 'buy' else -1)
    token_raw = -weth_raw * 1000
    amount0, amount1 = (weth_raw, token_raw) if weth_is_token0 else (token_raw, weth_raw)
    return {'pool': POOL, 'block': block if block is not None else ts, 'log_index': index,
            'transaction_hash': f'0x{ts:064x}', 'timestamp': ts, 'sender': ROUTER, 'recipient': ROUTER,
            'amount0': str(amount0), 'amount1': str(amount1), 'sqrt_price_x96': str(sqrt_price),
            'tick': 0, 'tx_from': wallet}


def test_decode_swap_signed_amounts_and_rejects_other_topics():
    event = decode_swap(swap_log(10 ** 17, -5 * 10 ** 20, block=77, index=3), 1_700_000_000)
    assert (event.pool, event.block, event.log_index, event.timestamp) == (POOL, 77, 3, 1_700_000_000)
    assert event.amount0 == 10 ** 17 and event.amount1 == -5 * 10 ** 20
    assert event.tick == -60 and event.sqrt_price_x96 == Q96 and event.sender == ROUTER
    with pytest.raises(ValueError):
        decode_swap({**swap_log(1, -1), 'topics': ['0x' + '00' * 32] * 3}, 1)


@pytest.mark.parametrize('weth_is_token0', [True, False])
def test_token_price_matches_pool_spot_math(weth_is_token0):
    sqrt_price = int(Q96 * 31.6227766)  # raw token1/token0 ratio of about 1000
    ratio = float(v3_token1_per_token0(sqrt_price, 18 if weth_is_token0 else 6, 6 if weth_is_token0 else 18))
    expected = 1 / ratio if weth_is_token0 else ratio
    assert token_price_weth(sqrt_price, weth_is_token0, 6) == pytest.approx(expected)
    assert token_price_weth(sqrt_price, weth_is_token0, None) is None


@pytest.mark.parametrize('weth_is_token0', [True, False])
def test_metrics_classify_count_and_respect_the_cutoff(weth_is_token0):
    rows = [row('buy', 1.0, 100, 'a', weth_is_token0=weth_is_token0),
            row('buy', 0.5, 110, 'b', weth_is_token0=weth_is_token0),
            row('sell', 0.25, 120, 'c', weth_is_token0=weth_is_token0),
            row('buy', 9.0, 200, 'late', weth_is_token0=weth_is_token0)]
    m = swap_metrics(rows, weth_is_token0=weth_is_token0, token_decimals=18, as_of=130,
                     eth_usd=2000, history_complete=True)
    assert (m['swap_count'], m['buy_count'], m['sell_count']) == (3, 2, 1)
    assert m['buy_volume_weth'] == 1.5 and m['sell_volume_weth'] == 0.25 and m['net_flow_weth'] == 1.25
    assert m['buy_volume_usd'] == 3000 and m['unique_buyers'] == 2 and m['unique_sellers'] == 1
    assert m['top_buyer_share_pct'] == pytest.approx(66.67, abs=.01)
    assert m['last_swap_age_seconds'] == 10 and m['trader_resolution_pct'] == 100


def test_unresolved_or_incomplete_history_withholds_wallet_metrics():
    rows = [row('buy', 1.0, 100, 'a'), row('buy', 1.0, 101, None)]
    m = swap_metrics(rows, weth_is_token0=True, token_decimals=18, as_of=110, history_complete=True)
    assert m['unique_buyers'] == 1 and m['top_buyer_share_pct'] is None
    assert m['round_trip_wallets'] is None and m['buyer_acceleration'] is None
    assert m['trader_resolution_pct'] == 50
    m = swap_metrics(rows[:1], weth_is_token0=True, token_decimals=18, as_of=110, history_complete=False)
    assert m['top_buyer_share_pct'] is None and m['swap_price_range_pct_60s'] is None


def test_acceleration_round_trips_and_per_second_peak():
    rows = [row('buy', .1, 45, 'a'), row('buy', .1, 75, 'b'), row('buy', .1, 80, 'c', index=1),
            row('buy', .1, 80, 'd', index=2), row('sell', .1, 80, 'a', index=3),
            row('buy', .1, 90, 'w', block=90), row('sell', .1, 90, 'w', block=90, index=1)]
    m = swap_metrics(rows, weth_is_token0=True, token_decimals=18, as_of=100, history_complete=True)
    assert m['unique_buyers_last_30s'] == 4 and m['unique_buyers_prev_30s'] == 1
    assert m['buyer_acceleration'] == 3 and m['peak_swaps_per_second'] == 3
    assert m['round_trip_wallets'] == 2 and m['same_block_round_trips'] == 1


def test_swap_price_range_starts_from_the_last_price_before_the_window():
    rows = [row('buy', .1, 10, 'a', sqrt_price=Q96), row('buy', .1, 80, 'b', sqrt_price=int(Q96 * 1.1))]
    m = swap_metrics(rows, weth_is_token0=False, token_decimals=18, as_of=100, history_complete=True)
    assert m['swap_price_range_pct_60s'] == pytest.approx(21.0, abs=.01)
    quiet = swap_metrics(rows[:1], weth_is_token0=False, token_decimals=18, as_of=100, history_complete=True)
    assert quiet['swap_price_range_pct_60s'] == 0.0


def test_store_dedupes_swaps_and_resolves_senders(tmp_path):
    store = Store(tmp_path)
    event = SwapEvent(POOL, 5, 0, '0x' + 'ab' * 32, 50, ROUTER, ROUTER, 1, -1, Q96, 1, 0)
    assert store.put_swaps([event, event]) == 1 and store.put_swaps([event]) == 0
    store.put_swaps([replace(event, block=9, log_index=1, transaction_hash='0x' + 'cd' * 32)])
    assert store.unresolved_transactions() == ['0x' + 'ab' * 32, '0x' + 'cd' * 32]
    store.set_transaction_sender('0x' + 'AB' * 32, '0x' + 'EE' * 20)
    assert store.swaps(POOL)[0]['tx_from'] == '0x' + 'ee' * 20
    assert [s['block'] for s in store.swaps(POOL, max_block=8)] == [5]
    assert store.get_meta('schema_version') == '2' and store.stats()['swaps'] == 2


def test_reader_requests_watched_pools_and_skips_removed_or_foreign_logs():
    rpc = Mock()
    rpc.logs.return_value = [swap_log(1, -1, blockTimestamp='0x64'),
                             swap_log(1, -1, index=1, removed=True),
                             swap_log(1, -1, index=2, pool='0x' + '99' * 20)]
    reader = ChainReader(rpc, '0x' + '01' * 20, WETH, None)
    events = reader.swaps([POOL.upper().replace('0X', '0x')], 5, 9)
    rpc.logs.assert_called_once_with([POOL], SWAP_TOPIC, 5, 9)
    assert len(events) == 1 and events[0].timestamp == 100
    rpc.block_timestamp.assert_not_called()
    assert reader.swaps([], 1, 2) == []


def make_monitor(tmp_path, **overrides):
    cfg = replace(load_config(), data_dir=tmp_path, decision_provider='stub', **overrides)
    monitor = Monitor(cfg, Store(tmp_path))
    return monitor, monitor.store


def close(monitor):
    monitor.rpc.close()
    monitor.price_feed.close()


def test_scan_records_swaps_from_pool_creation_and_marks_sync(tmp_path):
    monitor, store = make_monitor(tmp_path, max_block_range=10, backfill_blocks=10)
    created = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 95, 1_000, '0x' + 'dd' * 32, 0)
    monitor.rpc.block_number = Mock(return_value=100)
    monitor.reader.discover = Mock(return_value=[created])
    monitor.reader.swaps = Mock(return_value=[decode_swap(swap_log(10 ** 17, -10 ** 20, block=96), 1_001)])
    try:
        monitor.scan_once()
        monitor.reader.swaps.assert_called_once_with([POOL], 91, 100)
        assert store.get_meta(f'swap_coverage_from:{POOL}') == '95'
        assert monitor._synced['block'] == 100 and len(store.swaps(POOL)) == 1
    finally:
        close(monitor)


def test_catching_up_uses_the_cursor_block_time(tmp_path):
    monitor, _ = make_monitor(tmp_path, max_block_range=10, backfill_blocks=100)
    monitor.rpc.block_number = Mock(return_value=1000)
    monitor.reader.discover = Mock(return_value=[])
    monitor.reader.swaps = Mock(return_value=[])
    monitor.reader.block_time = Mock(return_value=12345)
    try:
        monitor.scan_once()
        monitor.reader.block_time.assert_called_once_with(930)
        assert monitor._synced == {'block': 930, 'at': 12345}
    finally:
        close(monitor)


def test_trader_lookup_is_bounded_and_pauses_on_rpc_errors(tmp_path):
    monitor, store = make_monitor(tmp_path, max_tx_lookups_per_cycle=2)
    base = SwapEvent(POOL, 1, 0, '0x' + '01' * 32, 1, ROUTER, ROUTER, 1, -1, Q96, 1, 0)
    store.put_swaps([replace(base, block=i, transaction_hash='0x' + f'{i:02x}' * 32) for i in range(1, 5)])
    monitor.rpc.transaction_sender = Mock(side_effect=['0x' + 'aa' * 20, None])
    try:
        monitor.resolve_traders()
        assert monitor.rpc.transaction_sender.call_count == 2
        assert [s['tx_from'] for s in store.swaps(POOL)] == ['0x' + 'aa' * 20, '', None, None]
        monitor.rpc.transaction_sender = Mock(side_effect=RpcError('limited'))
        monitor.resolve_traders()
        assert monitor.rpc.transaction_sender.call_count == 1
    finally:
        close(monitor)


def test_activity_uses_only_synced_blocks_and_reports_lag(tmp_path):
    monitor, store = make_monitor(tmp_path)
    event = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 10, 1_000, '0x' + 'dd' * 32, 0)
    store.set_meta(f'swap_coverage_from:{POOL}', '10')
    store.put_swaps([decode_swap(swap_log(10 ** 17, -10 ** 20, block=11, index=i, tx=t), 1_010)
                     for i, t in enumerate(('a1', 'a2'))] +
                    [decode_swap(swap_log(10 ** 17, -10 ** 20, block=30, tx='a3'), 1_030)])
    try:
        assert monitor._activity(event, {}, 1_040)['swap_tracking'] is False
        monitor._synced = {'block': 20, 'at': 1_036}
        state = monitor._activity(event, {'decimals': 18}, 1_040)
        assert state['swap_count'] == 2 and state['activity_lag_seconds'] == 4
        assert state['swap_history_complete'] is True and state['unique_buyers'] == 0
    finally:
        close(monitor)


def ready_state():
    now = time.time()
    return {'pool_address': POOL, 'token_address': TOKEN, 'price_usd': 1.0, 'quote_tokens_out': 25.0,
            'liquidity_usd': 30000.0, 'liquidity_verified': True, 'volatility_pct': 5.0,
            'price_impact_pct': .5, 'owner_renounced': True, 'contract_has_code': True,
            'eth_usd_verified': True, 'eth_usd_reference': 2500, 'eth_usd_timestamp': now,
            'quote_observed_at': now, 'swap_tracking': True, 'activity_lag_seconds': 1.0,
            'unique_buyers': 5, 'top_buyer_share_pct': 30.0}


def watch(monitor):
    monitor.active[POOL] = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 1, int(time.time()) - 5, '0x' + 'dd' * 32, 0)


def test_prefilter_does_not_spend_jev_credits_on_vetoed_pools(tmp_path):
    monitor, store = make_monitor(tmp_path)
    monitor.cfg = replace(monitor.cfg, decision_provider='jev')
    watch(monitor)
    monitor._state = Mock(return_value={**ready_state(), 'unique_buyers': 1})
    monitor.engine.decide = Mock(side_effect=AssertionError('JEV must not be called'))
    try:
        monitor.update_active()
        decision = store.decisions()[0]
        assert decision['model']['raw_answer']['sent'] is False
        assert decision['model']['raw_answer']['prefilter_reasons'] == ['too_few_or_unknown_unique_buyers']
        assert 'too_few_or_unknown_unique_buyers' in decision['gate']['reasons']
        assert store.stats()['positions'] == 0
    finally:
        close(monitor)


def test_prefilter_calls_jev_when_only_the_model_can_decide(tmp_path):
    monitor, store = make_monitor(tmp_path)
    monitor.cfg = replace(monitor.cfg, decision_provider='jev')
    watch(monitor)
    monitor._state = Mock(return_value=ready_state())
    monitor.engine.decide = Mock(return_value=({'action': 'buy', 'confidence': .9, 'risk_score': 2}, {}))
    try:
        monitor.update_active()
        monitor.engine.decide.assert_called_once()
        assert store.stats()['positions'] == 1
    finally:
        close(monitor)


def test_kill_switch_blocks_model_calls_and_paper_entries(tmp_path):
    monitor, store = make_monitor(tmp_path)
    watch(monitor)
    store.set_kill_switch(True)
    monitor._state = Mock(return_value=ready_state())
    monitor.engine.decide = Mock(side_effect=AssertionError('No model call while killed'))
    try:
        monitor.update_active()
        decision = store.decisions()[0]
        assert decision['action'] == 'skip' and 'kill_switch' in decision['gate']['reasons']
        assert store.stats()['positions'] == 0
        assert PaperExecutor(store).buy(ready_state()) == {'action': 'skip', 'reason': 'kill_switch'}
        store.set_kill_switch(False)
        assert PaperExecutor(store).buy(ready_state())['action'] == 'buy'
    finally:
        close(monitor)


def test_expired_pool_loses_its_swap_coverage_marker(tmp_path):
    monitor, store = make_monitor(tmp_path, monitor_minutes=1)
    monitor.active[POOL] = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 1, int(time.time()) - 600, '0x' + 'dd' * 32, 0)
    store.set_meta(f'swap_coverage_from:{POOL}', '1')
    try:
        monitor.update_active()
        assert POOL not in monitor.active and store.get_meta(f'swap_coverage_from:{POOL}') is None
    finally:
        close(monitor)
