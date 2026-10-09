from hoodscout.decision_engine import normalize_decision, DecisionEngine
from hoodscout.gates import evaluate, RiskLimits

VALID = {'contract_has_code': True, 'owner_renounced': True, 'liquidity_verified': True,
         'eth_usd_verified': True, 'eth_usd_reference': 2500, 'eth_usd_timestamp': 1000,
         'quote_observed_at': 1000, 'liquidity_usd': 40000, 'volatility_pct': 3, 'price_impact_pct': 1.5}
BUY = {'action': 'buy', 'confidence': .89, 'risk_score': 3}

def test_normalize_nested_jev():
    payload = {'answers':{'action':{'type':'choice','choice':'buy','confidence':.9},
                          'risk_score':{'type':'score','score':2,'confidence':.95}}}
    assert normalize_decision(payload) == {'action':'buy', 'confidence':.9, 'risk_score':3.0}


def test_invalid_responses_fail_closed():
    for data in ({}, {'action': {'choice':'buy','confidence':12},'risk_score':{'score':2}},
                 {'action': {'choice':'buy','confidence':.9},'risk_score':{'score':0}}):
        assert normalize_decision(data)['action']=='hold'


def test_default_stub_is_hold():
    decision,raw=DecisionEngine().decide({'token_address':'0x0'})
    assert decision['action']=='hold' and decision['confidence']==0


def test_gates_approved_only_if_all_true():
    assert evaluate(VALID,BUY,RiskLimits(), now=1000)['approved'] is True
    for key,val in [('contract_has_code',None),('owner_renounced',None),('liquidity_verified',False),
                    ('liquidity_usd',None),('volatility_pct',None),('volatility_pct',0.1),('price_impact_pct',None),('price_impact_pct',4)]:
        assert evaluate({**VALID,key:val},BUY,RiskLimits(), now=1000)['approved'] is False
    assert evaluate(VALID,{**BUY,'confidence':.69},RiskLimits(), now=1000)['approved'] is False
    assert evaluate(VALID,{**BUY,'risk_score':5},RiskLimits(), now=1000)['approved'] is False
    assert evaluate(VALID,{**BUY,'action':'hold'},RiskLimits(), now=1000)['approved'] is False

def test_live_mode_configuration_refused(monkeypatch):
    from hoodscout.config import load_config
    import pytest
    monkeypatch.setenv('LIVE_MODE','true')
    with pytest.raises(ValueError, match='refused'):
        load_config()
