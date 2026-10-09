"""Plug-compatible Jev engine; missing model credentials => HOLD, never BUY."""
from __future__ import annotations
from typing import Any
import httpx

QUESTIONS = {
    'action': {
        'type': 'choice', 'instructions': 'Given only information in this state at this timestamp, should a paper position be opened now?',
        'criteria': {'buy': 'Strong supported momentum, liquidity and low execution risk',
                     'hold': 'Insufficient evidence or missing critical measurements',
                     'skip': 'Red flags or weak opportunity'},
    },
    'risk_score': {'type': 'score', 'instructions': 'Rate market/token risk from 1 (lowest) to 10 (highest)', 'levels': 10},
}


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Reject unexpected API shapes/values; fail closed."""
    try:
        if 'answers' in raw:
            raw = raw['answers']
        action_data = raw['action']
        action = action_data.get('choice') if isinstance(action_data, dict) else action_data
        confidence = float(action_data['confidence'])
        risk_data = raw['risk_score']
        risk = float(risk_data.get('score') if isinstance(risk_data, dict) else risk_data)
        if action not in ('buy', 'hold', 'skip') or not 0 <= confidence <= 1 or not 1 <= risk <= 10:
            raise ValueError('out of bounds')
        return {'action': action, 'confidence': confidence, 'risk_score': risk}
    except (KeyError, ValueError, TypeError, AttributeError):
        return {'action': 'hold', 'confidence': 0.0, 'risk_score': 10.0, 'error': 'invalid_model_response'}


class DecisionEngine:
    def __init__(self, provider='stub', api_key=None,
                 endpoint='https://api.typesafe.ai/v1/systemone', model='jev-latest'):
        self.provider, self.api_key, self.endpoint, self.model = provider, api_key, endpoint, model

    def decide(self, state: dict) -> tuple[dict, dict]:
        if self.provider != 'jev' or not self.api_key:
            raw = {'provider': 'stub', 'reason': 'Connect JEV to enable model decisions'}
            return {'action': 'hold', 'confidence': 0.0, 'risk_score': 10.0}, raw
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(self.endpoint,
                    headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
                    json={'state': state, 'questions': QUESTIONS, 'model': self.model})
                response.raise_for_status()
                raw = response.json()
                if not isinstance(raw, dict):
                    raise ValueError('Expected JSON object')
                return normalize_decision(raw), raw
        except (httpx.HTTPError, ValueError) as exc:
            return {'action': 'hold', 'confidence': 0.0, 'risk_score': 10.0, 'error': 'model_unavailable'}, {'error': str(exc)[:250]}
