"""Local-only FastAPI dashboard and read-only control plane."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from .config import Config
from .store import Store
from .monitor import Monitor
from .replay import replay_pool, summarize_replay


HTML_FILE = Path(__file__).parent / 'static' / 'dashboard.html'


def create_app(cfg: Config, start_scanner: bool = True) -> FastAPI:
    store = Store(cfg.data_dir)
    monitor = Monitor(cfg, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scanner:
            monitor.start()
        try:
            yield
        finally:
            if start_scanner:
                monitor.stop()
            else:
                monitor.rpc.close()
                monitor.price_feed.close()

    app = FastAPI(title='HoodScout / Robinhood Chain', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.monitor = monitor
    app.state.config = cfg

    @app.get('/', response_class=HTMLResponse)
    def home():
        return HTML_FILE.read_text(encoding='utf-8')

    def bundle():
        stats = store.stats()
        positions = store.positions(50)
        mode = store.get_meta('display_mode') or 'PAPER'
        price_feed = monitor.price_feed.status()
        return {
            'timestamp': time.time(),
            'chain': 'Robinhood Chain', 'chain_id': cfg.chain_id,
            'mode': mode, 'live_execution_available': False,
            'scanner_online': monitor.ready and monitor.thread is not None and monitor.thread.is_alive(),
            'scanner_error': monitor.last_error or store.get_meta('scanner_last_error'),
            'scanner_last_ok': store.get_meta('scanner_last_ok'),
            'last_scanned_block': store.get_meta('last_scanned_block'),
            'active_pools': len(monitor.active),
            'decision_provider': cfg.decision_provider,
            'model_connected': monitor.engine.status()['connected'],
            'model_status': monitor.engine.status(),
            'eth_usd_reference_configured': price_feed['verified'],
            'price_feed': price_feed,
            'pool_discovery_topic_version': 2,
            'quoter_verified': monitor._quoter_verified is True,
            'quoter_configured': bool(cfg.quoter),
            'metrics': stats,
            'equity': store.equity_curve(),
            'positions': positions,
            'pools': store.recent_pools_with_state(40),
            'decisions': store.decisions(55),
        }

    @app.get('/api/state')
    def state():
        return bundle()

    @app.get('/api/stream')
    async def stream():
        async def event_stream():
            while True:
                yield 'data: ' + json.dumps(bundle(), separators=(',', ':'), default=str) + '\n\n'
                await asyncio.sleep(3)
        return StreamingResponse(event_stream(), media_type='text/event-stream',
                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/api/replay')
    def replay(pool: str = Query(..., min_length=42, max_length=42),
               entry_second: int = Query(60, ge=1, le=3600),
               hold_seconds: int = Query(300, ge=1, le=86400)):
        pool_rows = [p for p in store.pools(10000) if p['address'].lower() == pool.lower()]
        if not pool_rows:
            return {'status': 'unknown_pool'}
        return replay_pool(pool, pool_rows[0]['timestamp'], store.snapshots(pool),
                           entry_second, exit_after_seconds=hold_seconds,
                           notional_usd=cfg.paper_order_usd,
                           cost_bps_per_side=cfg.paper_slippage_bps + cfg.paper_fee_bps)

    @app.get('/api/backtest')
    def backtest(entry_second: int = Query(60, ge=1, le=120), hold_seconds: int = Query(300, ge=1, le=86400)):
        rows = store.pools(2000)
        results = [replay_pool(p['address'], p['timestamp'], store.snapshots(p['address']),
                         entry_second, exit_after_seconds=hold_seconds,
                         notional_usd=cfg.paper_order_usd,
                         cost_bps_per_side=cfg.paper_slippage_bps + cfg.paper_fee_bps)
                   for p in rows]
        return {'summary': summarize_replay(results), 'results': results}

    return app
