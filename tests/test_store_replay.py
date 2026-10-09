from pathlib import Path
from dataclasses import replace
from fastapi.testclient import TestClient
from hoodscout.config import load_config
from hoodscout.store import Store
from hoodscout.execution import PaperExecutor,LiveExecutor,LiveExecutionDisabled
from hoodscout.replay import replay_pool,summarize_replay
from hoodscout.webapp import create_app
import pytest

POOL='0x'+'45'*20
TOKEN='0x'+'56'*20


def test_store_and_paper(tmp_path):
    store=Store(tmp_path)
    event={'address':POOL,'token0':'0x'+'1'*40,'token1':TOKEN,
           'fee':3000,'tick_spacing':60,'timestamp':1000,'block':12,
           'transaction_hash':'0x'+'4'*64,'log_index':0}
    store.put_pool(event)
    store.put_pool(event)
    assert len(store.pools())==1
    state={'pool_address':POOL, 'token_address':TOKEN,'price_usd':2.,'quote_tokens_out':12.}
    store.snapshot(POOL,state,1200)
    assert len(store.snapshots(POOL))==1
    store.put_decision(POOL,state,{'action':'hold','confidence':0,'risk_score':10}, {}, {'approved':False,'action':'skip','reasons':['hold']},1200)
    assert 'skip' in (tmp_path/'decisions.jsonl').read_text()
    ex=PaperExecutor(store,order_usd=25,slippage_bps=100,fee_bps=30)
    assert ex.buy(state)['action']=='buy'
    assert ex.buy(state)['action']=='skip'
    assert len(store.open_positions())==1
    outcome=ex.mark({**state,'price_usd':5.},now=2000)
    assert outcome and outcome[0]['reason']=='TAKE_PROFIT'
    assert store.stats()['positions']==1 and store.stats()['closed_positions']==1


def test_replay_no_hindsight():
    history=[{'ts':59,'price_usd':1,'liquidity_verified':True},
             {'ts':60,'price_usd':2,'liquidity_verified':True},
             {'ts':120,'price_usd':4,'liquidity_verified':True},
             {'ts':200,'price_usd':50,'liquidity_verified':True}]
    result=replay_pool(POOL,0,history,60,exit_after_seconds=60,cost_bps_per_side=100)
    assert result['status']=='ok' and result['entry_price']==2.02
    assert result['exit_price']==3.96
    assert result['return_pct'] < 100
    assert replay_pool(POOL,0,history,30,exit_after_seconds=60)['status']=='insufficient_entry_data'
    assert replay_pool(POOL,0,history,60,exit_after_seconds=600)['status']=='insufficient_exit_data'
    summary=summarize_replay([result, {'status':'insufficient_exit_data'}])
    assert summary['usable']==1 and summary['excluded']==1


def test_live_execution_refuses_even_when_called():
    with pytest.raises(LiveExecutionDisabled):
        LiveExecutor().execute_buy({'token_address':TOKEN})


def test_app_responds_without_scanner(tmp_path):
    config=replace(load_config(),data_dir=tmp_path)
    app=create_app(config,start_scanner=False)
    with TestClient(app) as c:
        resp=c.get('/')
        assert resp.status_code==200 and 'HOODSCOUT' in resp.text
        status=c.get('/api/state').json()
        assert status['chain_id']==4663 and status['mode']=='PAPER'
        assert status['live_execution_available'] is False
        assert status['price_feed']['verified'] is False
        assert status['eth_usd_reference_configured'] is False
        assert status['pool_discovery_topic_version'] == 2
        assert c.get('/api/replay',params={'pool':POOL}).json()['status']=='unknown_pool'
        assert c.get('/api/backtest').json()['summary']['attempted']==0


def test_configured_model_is_not_reported_connected_before_request(tmp_path):
    config=replace(load_config(),data_dir=tmp_path,decision_provider='jev',typesafe_api_key='synthetic-secret')
    assert 'synthetic-secret' not in repr(config)
    with TestClient(create_app(config,start_scanner=False)) as client:
        state=client.get('/api/state').json()
        assert state['model_connected'] is False
        assert state['model_status']['status']=='not_checked'
        assert state['model_status']['configured'] is True
        assert 'synthetic-secret' not in client.get('/api/state').text
