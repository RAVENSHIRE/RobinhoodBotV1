"""Usage: python -m hoodscout.cli serve | scan | demo | backtest"""
from __future__ import annotations
import argparse
import logging
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.table import Table
from .config import load_config
from .store import Store
from .monitor import Monitor
from .replay import replay_pool, summarize_replay

console = Console()


def main():
    parser = argparse.ArgumentParser(description='Robinhood Chain pool monitor — paper trading only')
    parser.add_argument('command', choices=['serve', 'scan', 'demo', 'backtest', 'jev-check', 'jev-setup'])
    parser.add_argument('--no-scan', action='store_true', help='Dashboard without RPC connectivity')
    parser.add_argument('--entry-second', type=int, default=60)
    parser.add_argument('--hold-seconds', type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = load_config()
    if args.command in ('jev-check', 'jev-setup'):
        from .jev_setup import check_jev, setup_jev
        raise SystemExit((check_jev if args.command == 'jev-check' else setup_jev)(cfg))
    if args.command == 'demo':
        from .demo import seed_demo
        demo_path = Path('data/demo')
        demo_store = Store(demo_path)
        seed_demo(demo_store)
        console.print(f'[bold green]Seeded a SYNTHETIC demonstration[/] in {demo_path.resolve()}')
        console.print('[yellow]Run: DATA_DIR=./data/demo python -m hoodscout.cli serve --no-scan[/]')
        return
    store = Store(cfg.data_dir)
    if args.command == 'backtest':
        results = [replay_pool(p['address'], p['timestamp'], store.snapshots(p['address']),
                  args.entry_second, args.hold_seconds, cfg.paper_order_usd,
                  cfg.paper_slippage_bps + cfg.paper_fee_bps) for p in store.pools(2000)]
        console.print(summarize_replay(results))
        return
    if args.command == 'serve':
        import uvicorn
        from .webapp import create_app
        console.print(f'[bold]HOODSCOUT[/] — http://{cfg.host}:{cfg.port}  |  Robinhood Chain 4663  |  PAPER ONLY')
        uvicorn.run(create_app(cfg, start_scanner=not args.no_scan), host=cfg.host, port=cfg.port, log_level='info')
        return
    scanner = Monitor(cfg, store)
    scanner.start()
    console.print('[bold]HOODSCOUT[/] | Pool monitor | Ctrl+C to stop')
    table_interval = 10
    try:
        while True:
            status = store.stats()
            table = Table(title='Robinhood Chain / Monitor')
            table.add_column('Metric'); table.add_column('Value')
            for k, v in status.items():
                table.add_row(k, str(v))
            table.add_row('Active pools', str(len(scanner.active)))
            table.add_row('Scanner status', scanner.last_error or 'OK')
            console.print(table)
            time.sleep(table_interval)
    except KeyboardInterrupt:
        scanner.stop()


if __name__ == '__main__':
    main()
