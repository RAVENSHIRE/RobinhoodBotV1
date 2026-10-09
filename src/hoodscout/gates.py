"""Mandatory fail-closed risk gates independent of model advice."""
from __future__ import annotations
from dataclasses import dataclass
import math
import time


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class RiskLimits:
    min_liquidity_usd: float = 20_000
    min_volatility_pct: float = 1
    max_volatility_pct: float = 100
    min_confidence: float = 0.70
    max_risk_score: float = 4
    max_price_impact_pct: float = 3
    owner_renounced_required: bool = True
    max_price_age_seconds: float = 60.0
    max_quote_age_seconds: float = 15.0
    future_tolerance_seconds: float = 5.0
    min_unique_buyers: int = 3
    max_top_buyer_share_pct: float = 50.0
    max_activity_lag_seconds: float = 30.0


def evaluate(state: dict, decision: dict, limits: RiskLimits, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    reasons = []
    price_time = state.get('eth_usd_timestamp')
    if (state.get('eth_usd_verified') is not True or not finite(state.get('eth_usd_reference'))
            or state['eth_usd_reference'] <= 0 or not finite(price_time)
            or not -limits.future_tolerance_seconds <= now - price_time <= limits.max_price_age_seconds):
        reasons.append('missing_stale_or_unverified_eth_usd')
    quote_time = state.get('quote_observed_at')
    if (not finite(quote_time) or
            not -limits.future_tolerance_seconds <= now - quote_time <= limits.max_quote_age_seconds):
        reasons.append('missing_or_stale_quote')
    if decision.get('action') != 'buy':
        reasons.append('model_' + str(decision.get('action', 'unknown')))
    if state.get('contract_has_code') is not True:
        reasons.append('missing_or_unknown_code')
    if limits.owner_renounced_required and state.get('owner_renounced') is not True:
        reasons.append('owner_not_renounced_or_unknown')
    # Pool balances and slot0 are insufficient to establish swap execution quality.
    if state.get('liquidity_verified') is not True:
        reasons.append('no_verified_execution_quote')
    liquidity = state.get('liquidity_usd')
    if not finite(liquidity) or liquidity < limits.min_liquidity_usd:
        reasons.append('insufficient_or_unknown_liquidity')
    volatility = state.get('volatility_pct')
    if (not finite(volatility) or
        not limits.min_volatility_pct <= volatility <= limits.max_volatility_pct):
        reasons.append('insufficient_unknown_or_excessive_volatility')
    impact = state.get('price_impact_pct')
    if not finite(impact) or not 0 <= impact < limits.max_price_impact_pct:
        reasons.append('unacceptable_or_unknown_price_impact')
    # Launch activity comes from recorded Swap events; missing or lagging data is not "quiet".
    lag = state.get('activity_lag_seconds')
    if (state.get('swap_tracking') is not True or not finite(lag)
            or not -limits.future_tolerance_seconds <= lag <= limits.max_activity_lag_seconds):
        reasons.append('missing_or_stale_swap_activity')
    buyers = state.get('unique_buyers')
    if not finite(buyers) or buyers < limits.min_unique_buyers:
        reasons.append('too_few_or_unknown_unique_buyers')
    share = state.get('top_buyer_share_pct')
    if not finite(share) or not 0 <= share <= limits.max_top_buyer_share_pct:
        reasons.append('concentrated_or_unknown_buyer_share')
    confidence = decision.get('confidence')
    if not finite(confidence) or not 0 <= confidence <= 1 or confidence < limits.min_confidence:
        reasons.append('low_or_invalid_confidence')
    risk = decision.get('risk_score')
    if not finite(risk) or not 1 <= risk <= 10 or risk > limits.max_risk_score:
        reasons.append('high_or_invalid_risk')
    return {'approved': not reasons, 'reasons': reasons, 'action': 'buy' if not reasons else 'skip'}


# The strongest possible model answer: whatever this still rejects, no model answer can approve.
PREFILTER_DECISION = {'action': 'buy', 'confidence': 1.0, 'risk_score': 1.0}


def deterministic_reasons(state: dict, limits: RiskLimits, now: float | None = None) -> list[str]:
    return evaluate(state, PREFILTER_DECISION, limits, now)['reasons']
