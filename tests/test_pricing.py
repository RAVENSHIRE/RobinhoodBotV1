from datetime import datetime, timezone

import httpx
import pytest

from hoodscout.pricing import EthUsdFeed, coinbase_observation, kraken_observation


NOW = 1_800_000_000.0


def ticker(price='2500', timestamp=NOW - 2):
    return {'price': price, 'bid': '2499.99', 'ask': '2500.01',
            'time': datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}


def trades(price='2501', timestamp=NOW - 3):
    return {'error': [], 'result': {'XETHZUSD': [[price, '1', timestamp, 'b', 'm', '', 123]],
                                   'last': '1800000000999999999'}}


def make_feed(primary=None, secondary=None):
    clock = [NOW]
    requests = []

    def handler(request):
        requests.append(request)
        payload = (primary if primary is not None else ticker()) if request.url.host == 'api.exchange.coinbase.com' else (
            secondary if secondary is not None else trades())
        return httpx.Response(200, json=payload)

    feed = EthUsdFeed(client=httpx.Client(transport=httpx.MockTransport(handler)),
                      clock=lambda: clock[0], monotonic=lambda: clock[0])
    return feed, clock, requests


def test_two_sources_provenance_throttle_and_expiry_without_new_request():
    feed, clock, requests = make_feed()
    try:
        result = feed.refresh()
        assert result['verified'] and result['price_usd'] == 2500
        assert result['timestamp'] == NOW - 3
        assert [s['source'] for s in result['sources']] == ['coinbase', 'kraken']
        assert result['divergence_pct'] < 1
        assert requests[1].url.params['pair'] == 'ETHUSD'
        feed.refresh()
        assert len(requests) == 2
        clock[0] += 61
        expired = feed.status()
        assert expired['price_usd'] is None and not expired['verified']
        assert expired['error'] == 'stale_source_price'
        assert len(requests) == 2
    finally:
        feed.close()


@pytest.mark.parametrize('primary,secondary,reason', [
    (ticker(timestamp=NOW-61), trades(), 'stale_source_price'),
    (ticker(), trades(timestamp=NOW-61), 'stale_source_price'),
    (ticker(timestamp=NOW+6), trades(), 'future_source_timestamp'),
    (ticker(), trades(timestamp=NOW+6), 'future_source_timestamp'),
    (ticker(), trades(price='2600'), 'source_price_disagreement'),
])
def test_feed_rejects_stale_future_and_disagreeing_observations(primary, secondary, reason):
    feed, _, _ = make_feed(primary, secondary)
    try:
        result = feed.refresh()
        assert not result['verified'] and result['price_usd'] is None
        assert result['error'] == reason
    finally:
        feed.close()


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '0', '-1', True, None])
def test_invalid_provider_prices_fail_closed(value):
    feed, _, _ = make_feed(ticker(price=value))
    try:
        result = feed.refresh()
        assert not result['verified'] and result['price_usd'] is None
        assert result['error'].startswith('feed_unavailable')
    finally:
        feed.close()


def test_failed_refresh_invalidates_previous_good_reading_immediately():
    clock = [NOW]
    failed = [False]

    def handler(request):
        if request.url.host == 'api.kraken.com':
            return httpx.Response(429) if failed[0] else httpx.Response(200, json=trades())
        return httpx.Response(200, json=ticker())

    feed = EthUsdFeed(client=httpx.Client(transport=httpx.MockTransport(handler)),
                      clock=lambda: clock[0], monotonic=lambda: clock[0])
    try:
        assert feed.refresh()['verified']
        clock[0] += 10
        failed[0] = True
        assert not feed.refresh()['verified']
        assert feed.status()['price_usd'] is None
        assert feed.status()['sources'] == []
    finally:
        feed.close()


def test_trade_timestamp_is_used_instead_of_kraken_cursor():
    observation = kraken_observation(trades(timestamp=NOW-500))
    assert observation.timestamp == NOW-500
    with pytest.raises(ValueError):
        kraken_observation({'error': ['EGeneral:Unavailable'], 'result': {}})
    with pytest.raises(ValueError):
        coinbase_observation({**ticker(), 'time': '2026-10-09T16:00:00'})


def test_disabled_feed_does_not_make_network_requests():
    feed, _, requests = make_feed()
    feed.enabled = False
    try:
        assert not feed.refresh()['verified']
        assert requests == []
    finally:
        feed.close()
