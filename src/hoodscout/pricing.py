"""Timestamped ETH/USD research reference; no stale or single-source fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import time

import httpx


COINBASE_URL = 'https://api.exchange.coinbase.com/products/ETH-USD/ticker'
KRAKEN_URL = 'https://api.kraken.com/0/public/Trades'


def number(value) -> float:
    if isinstance(value, bool):
        raise ValueError('Boolean is not a price or timestamp')
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Expected a positive finite number')
    return result


@dataclass(frozen=True)
class Observation:
    source: str
    price: float
    timestamp: float


def coinbase_observation(payload: dict) -> Observation:
    price = number(payload['price'])
    bid, ask = number(payload['bid']), number(payload['ask'])
    if bid > ask:
        raise ValueError('Coinbase bid exceeds ask')
    stamp = datetime.fromisoformat(payload['time'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Coinbase timestamp lacks timezone')
    return Observation('coinbase', price, number(stamp.timestamp()))


def kraken_observation(payload: dict) -> Observation:
    if payload.get('error') != []:
        raise ValueError('Kraken returned an error')
    pairs = [rows for key, rows in payload['result'].items() if key != 'last']
    if len(pairs) != 1 or not pairs[0]:
        raise ValueError('Kraken did not return one ETH/USD trade series')
    # Use trade time, not response time or the pagination cursor.
    observations = [Observation('kraken', number(row[0]), number(row[2])) for row in pairs[0]]
    return max(observations, key=lambda item: item.timestamp)


class EthUsdFeed:
    def __init__(self, *, enabled=True, refresh_seconds=10.0, max_age_seconds=60.0,
                 max_divergence_pct=1.0, future_tolerance_seconds=5.0,
                 client=None, clock=time.time, monotonic=time.monotonic):
        self.enabled = enabled
        self.refresh_seconds = refresh_seconds
        self.max_age_seconds = max_age_seconds
        self.max_divergence_pct = max_divergence_pct
        self.future_tolerance_seconds = future_tolerance_seconds
        self.client = client or httpx.Client(timeout=5.0, headers={'User-Agent': 'HoodScout/0.2'})
        self.clock, self.monotonic = clock, monotonic
        self._next_attempt = 0.0
        # Immutable tuple replaced atomically; dashboard reads never perform network I/O.
        self._reading = ((), None, 'not_checked' if enabled else 'disabled')

    def close(self):
        self.client.close()

    def refresh(self) -> dict:
        if not self.enabled or self.monotonic() < self._next_attempt:
            return self.status()
        try:
            response = self.client.get(COINBASE_URL)
            response.raise_for_status()
            primary = coinbase_observation(response.json())
            response = self.client.get(KRAKEN_URL, params={'pair': 'ETHUSD', 'count': 1})
            response.raise_for_status()
            secondary = kraken_observation(response.json())
            self._reading = ((primary, secondary), self.clock(), None)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
            # Discard previous good values immediately if either provider fails.
            self._reading = ((), self.clock(), f'feed_unavailable: {type(exc).__name__}')
        finally:
            self._next_attempt = self.monotonic() + self.refresh_seconds
        return self.status()

    def status(self, now=None) -> dict:
        now = self.clock() if now is None else now
        observations, checked_at, error = self._reading
        sources = [{'source': o.source, 'price_usd': o.price, 'timestamp': o.timestamp,
                    'age_seconds': now - o.timestamp} for o in observations]
        divergence = None
        if len(observations) == 2:
            ages = [now - o.timestamp for o in observations]
            a, b = (o.price for o in observations)
            divergence = abs(a - b) / ((a + b) / 2) * 100
            if any(age < -self.future_tolerance_seconds for age in ages):
                error = 'future_source_timestamp'
            elif any(age > self.max_age_seconds for age in ages):
                error = 'stale_source_price'
            elif divergence > self.max_divergence_pct:
                error = 'source_price_disagreement'
        verified = len(observations) == 2 and error is None
        return {
            'provider': 'coinbase_kraken' if self.enabled else 'off',
            'verified': verified,
            'price_usd': observations[0].price if verified else None,
            'timestamp': min(o.timestamp for o in observations) if observations else None,
            'checked_at': checked_at,
            'max_age_seconds': self.max_age_seconds,
            'divergence_pct': divergence,
            'max_divergence_pct': self.max_divergence_pct,
            'sources': sources, 'error': error,
        }
