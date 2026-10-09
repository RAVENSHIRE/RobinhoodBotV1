from dataclasses import replace
from unittest.mock import Mock
from hoodscout.config import load_config
from hoodscout.store import Store
from hoodscout.monitor import Monitor
from hoodscout.chain import PoolEvent
from pathlib import Path

WETH='0x0bd7d308f8e1639fab988df18a8011f41eacad73'
TOKEN='0x'+'11'*20
POOL='0x'+'22'*20


def test_monitor_persists_cursor_and_pools(tmp_path):
    cfg=replace(load_config(),data_dir=tmp_path,max_block_range=10,backfill_blocks=10)
    store=Store(tmp_path)
    monitor=Monitor(cfg,store)
    monitor.rpc.block_number=Mock(return_value=100)
    event=PoolEvent(POOL,WETH,TOKEN,10000,200,95,1600000000,'0x'+'dd'*32,0)
    monitor.reader.discover=Mock(return_value=[event])
    monitor.scan_once()
    assert store.get_meta('last_scanned_block')=='100'
    assert store.pools()[0]['address']==POOL
    assert POOL.lower() in monitor.active
    monitor.rpc.close()
    monitor.price_feed.close()


def test_old_cursor_is_recovered_with_a_bounded_window_and_new_cursor_is_reused(tmp_path):
    cfg = replace(load_config(), data_dir=tmp_path, backfill_blocks=10, max_block_range=10)
    store = Store(tmp_path)
    store.set_meta('last_scanned_block', '100')
    monitor = Monitor(cfg, store)
    monitor.rpc.block_number = Mock(return_value=100)
    monitor.reader.discover = Mock(return_value=[])
    try:
        monitor.scan_once()
        monitor.reader.discover.assert_called_once_with(91, 100)
        assert store.get_meta('pool_created_cursor_v2') == '100'
        monitor._cursor = None  # Simulate reopening the persisted cursor.
        monitor.rpc.block_number.return_value = 103
        monitor.scan_once()
        monitor.reader.discover.assert_called_with(101, 103)
    finally:
        monitor.rpc.close()
        monitor.price_feed.close()


def test_rejected_quoter_stays_rejected_on_repeated_attempts(tmp_path):
    import pytest
    from hoodscout.rpc import RpcError, address_word
    cfg = replace(load_config(), data_dir=tmp_path, quoter='0x' + '33'*20)
    monitor = Monitor(cfg, Store(tmp_path))
    monitor.rpc.code = Mock(return_value=True)
    monitor.rpc.eth_call = Mock(return_value='0x' + address_word('0x' + 'ff'*20))
    try:
        for _ in range(2):
            with pytest.raises(RpcError, match='different factory'):
                monitor._verify_quoter()
    finally:
        monitor.rpc.close()
        monitor.price_feed.close()


def test_unavailable_feed_cannot_use_legacy_static_price_or_request_quote(tmp_path):
    cfg = replace(load_config(), data_dir=tmp_path, eth_usd=2500, quoter='0x'+'33'*20)
    monitor = Monitor(cfg, Store(tmp_path))
    monitor.price_feed.refresh = Mock(return_value={'verified': False, 'price_usd': None,
                                                  'timestamp': None, 'error': 'stale_source_price'})
    monitor.reader.pool_state = Mock(return_value={'price_usd': None, 'sqrt_price_x96': str(2**96),
                                                  'tvl_usd_estimate': None})
    monitor.rpc.eth_call = Mock(side_effect=AssertionError('No quote may be requested'))
    event = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 1, 1, '0x'+'dd'*32, 0)
    try:
        state = monitor._state(event)
        assert monitor.reader.eth_usd is None
        assert state['eth_usd_verified'] is False
        assert state['price_usd'] is None and not state['liquidity_verified']
        monitor.rpc.eth_call.assert_not_called()
    finally:
        monitor.rpc.close()
        monitor.price_feed.close()


def test_failed_quote_blocks_a_model_buy(tmp_path):
    import time
    from unittest.mock import patch
    from hoodscout.rpc import RpcError
    cfg = replace(load_config(), data_dir=tmp_path, quoter='0x'+'33'*20)
    store = Store(tmp_path)
    monitor = Monitor(cfg, store)
    event = PoolEvent(POOL, WETH, TOKEN, 3000, 60, 1, int(time.time())-60, '0x'+'dd'*32, 0)
    monitor.active[POOL] = event
    monitor.price_feed.refresh = Mock(return_value={'verified': True, 'price_usd': 2500,
                                                   'timestamp': time.time()})
    monitor.reader.pool_state = Mock(return_value={'pool_address': POOL, 'token_address': TOKEN,
        'price_usd': 1, 'sqrt_price_x96': str(2**96), 'tvl_usd_estimate': 40000,
        'token0_metadata': {'decimals': 18}, 'token1_metadata': {'decimals': 18},
        'contract_has_code': True, 'owner_renounced': True, 'liquidity_verified': False})
    monitor._verify_quoter = Mock(return_value=True)
    monitor.engine.decide = Mock(return_value=({'action': 'buy', 'confidence': .9, 'risk_score': 2}, {}))
    try:
        with patch('hoodscout.monitor.quote_buy', side_effect=RpcError('execution reverted')):
            monitor.update_active()
        assert store.stats()['positions'] == 0
        decision = store.decisions()[0]
        assert 'no_verified_execution_quote' in decision['gate']['reasons']
        assert decision['state']['quote_error'] == 'execution reverted'
    finally:
        monitor.rpc.close()
        monitor.price_feed.close()

def test_monitor_decision_to_paper_pipeline(tmp_path):
    import time
    cfg=replace(load_config(),data_dir=tmp_path)
    store=Store(tmp_path)
    monitor=Monitor(cfg,store)
    event=PoolEvent(POOL,WETH,TOKEN,10000,200,95,int(time.time())-65,'0x'+'dd'*32,0)
    monitor.active[POOL.lower()]=event
    state={'pool_address':POOL,'token_address':TOKEN,'symbol':'TEST', 'price_usd':1.0,
           'quote_tokens_out':25.0,'liquidity_usd':30000.0, 'liquidity_verified':True,
           'volatility_pct':5.0,'price_impact_pct':0.5,'owner_renounced':True,
           'contract_has_code':True, 'eth_usd_verified':True, 'eth_usd_reference':2500,
           'eth_usd_timestamp':time.time(), 'quote_observed_at':time.time()}
    monitor._state=Mock(return_value=state)
    monitor.engine.decide=Mock(return_value=({'action':'buy','confidence':0.87,'risk_score':2}, {'model':'mock'}))
    monitor.update_active()
    assert store.stats()['decisions']==1
    assert store.stats()['positions']==1
    assert store.positions()[0]['mode']=='PAPER'
    monitor.update_active()
    assert store.stats()['decisions']==1
    monitor.rpc.close()
    monitor.price_feed.close()
