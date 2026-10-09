import pytest

from hoodscout.gates import evaluate, RiskLimits


STATE = {'contract_has_code': True, 'owner_renounced': True,
         'liquidity_verified': True, 'liquidity_usd': 40000,
         'volatility_pct': 3, 'price_impact_pct': 1,
         'eth_usd_verified': True, 'eth_usd_reference': 2500,
         'eth_usd_timestamp': 995, 'quote_observed_at': 999}
BUY = {'action': 'buy', 'confidence': .9, 'risk_score': 2}


@pytest.mark.parametrize('field,value,reason', [
    ('eth_usd_verified', False, 'missing_stale_or_unverified_eth_usd'),
    ('eth_usd_reference', float('nan'), 'missing_stale_or_unverified_eth_usd'),
    ('eth_usd_timestamp', None, 'missing_stale_or_unverified_eth_usd'),
    ('eth_usd_timestamp', 939, 'missing_stale_or_unverified_eth_usd'),
    ('eth_usd_timestamp', 1006, 'missing_stale_or_unverified_eth_usd'),
    ('quote_observed_at', 984, 'missing_or_stale_quote'),
    ('quote_observed_at', 1006, 'missing_or_stale_quote'),
    ('quote_observed_at', None, 'missing_or_stale_quote'),
    ('liquidity_verified', False, 'no_verified_execution_quote'),
    ('price_impact_pct', 3, 'unacceptable_or_unknown_price_impact'),
    ('price_impact_pct', 97.687, 'unacceptable_or_unknown_price_impact'),
    ('price_impact_pct', float('nan'), 'unacceptable_or_unknown_price_impact'),
    ('liquidity_usd', float('inf'), 'insufficient_or_unknown_liquidity'),
])
def test_buy_cannot_override_missing_or_expired_evidence(field, value, reason):
    assert evaluate(STATE, BUY, RiskLimits(), now=1000)['approved']
    result = evaluate({**STATE, field: value}, BUY, RiskLimits(), now=1000)
    assert not result['approved'] and reason in result['reasons']


def test_model_latency_expires_a_quote_before_execution():
    assert evaluate(STATE, BUY, RiskLimits(), now=1000)['approved']
    result = evaluate(STATE, BUY, RiskLimits(), now=1015)
    assert not result['approved'] and 'missing_or_stale_quote' in result['reasons']
