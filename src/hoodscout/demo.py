"""Explicit, isolated synthetic UI sample. This is NOT historical chain data."""
from __future__ import annotations
import math
import random
import time
from .store import Store


def seed_demo(store: Store):
    rng = random.Random(4663)
    now = time.time()
    store.set_meta('display_mode', 'SYNTHETIC DEMO')
    store.set_meta('last_scanned_block', 'DEMO / NOT CONNECTED')
    for i in range(20):
        pool = '0x' + format(i + 4663, '040x')
        token = '0x' + format(i + 2222, '040x')
        timestamp = now - 1800 + i * 50
        event = {'address': pool, 'token0': '0x0bd7d308f8e1639fab988df18a8011f41eacad73',
                 'token1': token, 'fee': 10000, 'tick_spacing': 200,
                 'block': 600000 + i, 'timestamp': int(timestamp),
                 'transaction_hash': '0x' + format(i + 1, '064x'), 'log_index': 0}
        store.put_pool(event)
        base = (0.000001 + i * 0.0000003)
        winner = i % 4 != 0
        for j in range(12):
            elapsed = j * 30
            move = (1 + elapsed / 450 * (2.5 if winner else -0.7)) * (1 + 0.05 * math.sin(j + i))
            price = max(base * move, base * 0.1)
            state = {**event, 'pool_address': pool, 'token_address': token, 'symbol': f'HOOD{i:02}',
                     'observed_at': timestamp + elapsed, 'age_seconds': elapsed, 'price_usd': price,
                     'liquidity_usd': float(20000 + i * 2700), 'tvl_usd_estimate': float(20000 + i * 2700),
                     'liquidity_verified': True, 'price_impact_pct': 0.4 + i * 0.07,
                     'volatility_pct': 2.1 + 0.2 * j, 'owner_renounced': True,
                     'contract_has_code': True, 'quote_tokens_out': 20 / price}
            store.snapshot(pool, state, timestamp + elapsed)
            if j == 2:
                action = 'buy' if i % 3 == 0 else 'skip'
                model = {'action': action, 'confidence': 0.89 if action == 'buy' else 0.46, 'risk_score': 2 if winner else 5}
                gate = {'approved': action == 'buy' and winner, 'action': 'buy' if action == 'buy' and winner else 'skip',
                        'reasons': [] if action == 'buy' and winner else ['synthetic_demo_skip']}
                store.put_decision(pool, state, model, {'provider': 'SYNTHETIC DEMO'}, gate, timestamp + elapsed)
        if winner and i % 3 == 0:
            store.open_position(pool, token, 25.0, 25.0 / (base * 1.1), base * 1.1, timestamp + 60)
            pos = store.open_positions()
            matching = next((p for p in pos if p['pool'] == pool), None)
            if matching:
                profit = 22.5 + i * 2.7
                price = (25.0 + profit) / matching['units']
                store.close_position(matching['id'], price, profit, 'DEMO_TAKE_PROFIT', timestamp + 350)
