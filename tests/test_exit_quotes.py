from dataclasses import replace
from unittest.mock import Mock
import pytest

from hoodscout.chain import PoolEvent
from hoodscout.config import load_config
from hoodscout.execution import PaperExecutor
from hoodscout.monitor import Monitor
from hoodscout.quote import quote_sell, encode_quote_exact_input_single
from hoodscout.store import Store

WETH='0x'+'11'*20
TOKEN='0x'+'22'*20
POOL='0x'+'33'*20
QUOTER='0x'+'44'*20


def event():
    return PoolEvent(POOL,WETH,TOKEN,3000,60,1,900,'0x'+'dd'*32,0)


def test_sell_quote_reverses_pair_and_preserves_quantity_precision():
    rpc=Mock()
    rpc.eth_call.return_value='0x'+''.join(format(n,'064x') for n in (10**16,2**96,1,70000))
    quote=quote_sell(rpc,QUOTER,event(),WETH,2500,1.1234567,6)
    rpc.eth_call.assert_called_once_with(QUOTER,encode_quote_exact_input_single(TOKEN,WETH,1123456,3000))
    assert quote['proceeds_usd']==25 and quote['amount_in_raw']=='1123456'
    rpc.eth_call.reset_mock()
    reversed_event=replace(event(),token0=TOKEN,token1=WETH)
    quote_sell(rpc,QUOTER,reversed_event,WETH,2500,1.1234567,6)
    rpc.eth_call.assert_called_once_with(QUOTER,encode_quote_exact_input_single(TOKEN,WETH,1123456,3000))


@pytest.mark.parametrize('units,decimals,price',[(float('nan'),18,2500),(1,None,2500),
    (1,37,2500),(1,True,2500),(1,18,0),(1,18,float('inf')),(1e-20,6,2500),
    (True,18,2500),(None,18,2500)])
def test_invalid_sell_input_never_calls_rpc(units,decimals,price):
    rpc=Mock()
    with pytest.raises(ValueError):
        quote_sell(rpc,QUOTER,event(),WETH,price,units,decimals)
    rpc.eth_call.assert_not_called()


def position(tmp_path):
    store=Store(tmp_path)
    store.open_position(POOL,TOKEN,25,10,2.5,ts=100)
    p=store.open_positions()[0]
    quote={'pool_address':POOL,'token_address':TOKEN,'units':10,'observed_at':1000,'proceeds_usd':50}
    state={'pool_address':POOL,'price_usd':1e9,'eth_usd_verified':True,'eth_usd_timestamp':1000,
           'exit_quotes':{str(p['id']):quote}}
    return store,state,quote


def test_spot_profit_cannot_fake_exit_and_quote_uses_sell_proceeds(tmp_path):
    store,state,quote=position(tmp_path)
    ex=PaperExecutor(store,slippage_bps=100,fee_bps=30)
    assert ex.mark({**state,'exit_quotes':{}},now=1000)==[]
    assert len(store.open_positions())==1
    quote['proceeds_usd']=10
    result=ex.mark(state,now=1000)
    assert result[0]['reason']=='STOP_LOSS'
    assert result[0]['pnl_usd']==pytest.approx(-15.13)


@pytest.mark.parametrize('field,value',[('observed_at',984),('observed_at',1006),
    ('observed_at',float('nan')),('units',11),('pool_address',TOKEN),('token_address',WETH),
    ('proceeds_usd',float('inf')),('proceeds_usd',True),('proceeds_usd',0)])
def test_stale_mismatched_or_invalid_quotes_leave_position_open(tmp_path,field,value):
    store,state,quote=position(tmp_path)
    quote[field]=value
    assert PaperExecutor(store).mark(state,now=1000)==[]
    assert store.open_positions()


def test_stale_reference_blocks_exit_but_kill_switch_does_not(tmp_path):
    store,state,quote=position(tmp_path)
    ex=PaperExecutor(store)
    assert ex.mark({**state,'eth_usd_timestamp':939},now=1000)==[]
    store.set_kill_switch(True)
    assert ex.mark(state,now=1000)[0]['reason']=='TAKE_PROFIT'


def test_monitor_requests_exit_quote_even_if_buy_quote_fails(tmp_path,monkeypatch):
    from hoodscout.rpc import RpcError
    cfg=replace(load_config(),data_dir=tmp_path,weth=WETH,quoter=QUOTER)
    store=Store(tmp_path); store.open_position(POOL,TOKEN,25,10,2.5)
    m=Monitor(cfg,store)
    import time
    m.price_feed.refresh=Mock(return_value={'verified':True,'price_usd':2500,'timestamp':time.time()})
    m.reader.pool_state=Mock(return_value={'pool_address':POOL,'price_usd':2.5,
        'decimals':18,'sqrt_price_x96':str(2**96),'tvl_usd_estimate':40000,
        'token0_metadata':{'decimals':18},'token1_metadata':{'decimals':18}})
    m._verify_quoter=Mock(return_value=True)
    monkeypatch.setattr('hoodscout.monitor.quote_buy',Mock(side_effect=RpcError('buy unavailable')))
    sell=Mock(return_value={'proceeds_usd':24})
    monkeypatch.setattr('hoodscout.monitor.quote_sell',sell)
    try:
        state=m._state(event())
        assert state['exit_quotes'] and state['quote_error']=='buy unavailable'
        sell.assert_called_once()
    finally:
        m.rpc.close(); m.price_feed.close()
