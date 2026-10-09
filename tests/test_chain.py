from decimal import Decimal
from hoodscout.chain import (
    POOL_CREATED_TOPIC, decode_pool_created, v3_token1_per_token0, calculate_usd_state
)
from hoodscout.rpc import read_word, as_address, read_string, address_word
from hoodscout.quote import encode_quote_exact_input_single

WETH='0x0bd7d308f8e1639fab988df18a8011f41eacad73'
TOKEN='0x'+'ab'*20
POOL='0x'+'cd'*20

def word(value): return format(value, '064x')

def test_pool_created_decoding():
    log = {'topics':['0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118','0x'+address_word(WETH),'0x'+address_word(TOKEN),'0x'+word(10000)],
           'data':'0x'+word(200)+address_word(POOL), 'blockNumber': '0x10',
           'transactionHash':'0x'+'99'*32, 'logIndex':'0x2'}
    result = decode_pool_created(log, 12345)
    assert result.address == POOL
    assert result.fee == 10000 and result.tick_spacing == 200
    assert result.block == 16 and result.timestamp == 12345


def test_invalid_event_rejected():
    import pytest
    with pytest.raises(ValueError):
        decode_pool_created({'topics':['0xdead'], 'data':'0x'}, 0)


def test_independent_robinhood_mainnet_factory_event():
    # Captured by eth_getLogs from the configured factory at block 9490,
    # independently of the application's former (incorrect) topic constant.
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/robinhood_pool_created.json').read_text())
    event = decode_pool_created(fixture['log'], fixture['timestamp'])
    assert event.address == '0x8be7e807fba21f5c16d2f0079b1a7c84c0866ea5'
    assert event.block == 9490 and event.fee == 3000 and event.tick_spacing == 60
    assert event.token0 == WETH
    assert event.token1 == '0x99f381b8bcd5b367178809abdbb7ae79da782e0e'


def test_price_and_tvl_proxy():
    sqrt_price = 10 * 2**96   # 100 TOKEN / WETH, 18 decimals each
    assert v3_token1_per_token0(sqrt_price,18,18) == Decimal(100)
    price,tvl=calculate_usd_state(WETH,TOKEN,WETH,18,18,sqrt_price,10**18,100*10**18,1000)
    assert price == 10
    assert tvl == 2000
    assert calculate_usd_state(TOKEN,POOL,WETH,18,18,sqrt_price,0,0,1000)==(None,None)
    assert calculate_usd_state(WETH,TOKEN,WETH,18,18,0,0,0,1000)==(None,None)


def test_abi_read_helpers():
    assert read_word('0x'+word(42)) == 42
    assert as_address(0) == '0x'+'0'*40
    payload='0x'+word(32)+word(4)+('TEST'.encode().hex()).ljust(64,'0')
    assert read_string(payload)=='TEST'
    assert read_string('0x'+'454243'.ljust(64,'0'))=='EBC'


def test_quote_encoding():
    data=encode_quote_exact_input_single(WETH,TOKEN,10000,10000)
    assert data.startswith('0xc6a5026a') and len(data)==10+64*5
    assert data.endswith('0'*64)

def test_quoter_math_with_mocked_return():
    from hoodscout.quote import quote_buy
    from hoodscout.chain import PoolEvent
    from unittest.mock import Mock
    event=PoolEvent(POOL,WETH,TOKEN,10000,200,10,1000,'0x'+'77'*32,0)
    rpc=Mock()
    # 0.025 WETH in, expected 2.5 TOKEN out at 100 token/WETH.
    # After 1% fee and 1% price impact => 2.45025 tokens.
    raw_out=int(2.45025*10**18)
    rpc.eth_call.return_value='0x'+word(raw_out)+word(9*2**96)+word(0)+word(100000)
    result=quote_buy(rpc,'0x'+'ed'*20,event,WETH,1000,25,10*2**96,18,18)
    assert 0.99 < result['price_impact_pct'] < 1.01
    assert result['quote_usd']==25
    assert result['quote_tokens_out']>2.45
