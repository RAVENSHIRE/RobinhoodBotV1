"""SQLite persisted scan stream and append-only audit JSONL. No wallet access."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'hoodscout.db'
        self.audit = self.dir / 'decisions.jsonl'
        self.lock = threading.RLock()
        with self._connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pools (
              address TEXT PRIMARY KEY, token0 TEXT, token1 TEXT, fee INTEGER,
              timestamp INTEGER, block INTEGER, event_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT NOT NULL, ts REAL NOT NULL,
              state_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS snapshots_pool_ts ON snapshots(pool, ts);
            CREATE TABLE IF NOT EXISTS decisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, pool TEXT NOT NULL,
              action TEXT NOT NULL, confidence REAL NOT NULL, risk_score REAL NOT NULL,
              state_json TEXT NOT NULL, model_json TEXT NOT NULL, gate_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS positions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT NOT NULL, token TEXT NOT NULL,
              opened REAL NOT NULL, closed REAL, size_usd REAL NOT NULL,
              units REAL NOT NULL, entry_price REAL NOT NULL, exit_price REAL,
              realized_pnl REAL, exit_reason TEXT, mode TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS swaps (
              pool TEXT NOT NULL, block INTEGER NOT NULL, log_index INTEGER NOT NULL,
              tx_hash TEXT NOT NULL, ts INTEGER NOT NULL, sender TEXT NOT NULL,
              recipient TEXT NOT NULL, amount0 TEXT NOT NULL, amount1 TEXT NOT NULL,
              sqrt_price_x96 TEXT NOT NULL, liquidity TEXT NOT NULL, tick INTEGER NOT NULL,
              tx_from TEXT, PRIMARY KEY (pool, block, log_index));
            CREATE INDEX IF NOT EXISTS swaps_pool_block ON swaps(pool, block, log_index);
            CREATE INDEX IF NOT EXISTS swaps_tx_hash ON swaps(tx_hash);
            ''')
            # Additive migrations only: existing pools, decisions and positions are preserved.
            db.execute("INSERT INTO metadata VALUES('schema_version','2') "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE CAST(value AS INTEGER) < 2")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def get_meta(self, key: str) -> str | None:
        with self.lock, self._connect() as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return row['value'] if row else None

    def set_meta(self, key: str, value: str):
        with self.lock, self._connect() as db:
            db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def delete_meta(self, key: str):
        with self.lock, self._connect() as db:
            db.execute('DELETE FROM metadata WHERE key=?', (key,))

    def reserve_model_call(self, provider: str, day: str, limit: int) -> bool:
        """Count attempts before sending, atomically across processes and restarts."""
        key = f'model_calls:{provider}:{day}'
        with self.lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            count = int(row[0]) if row else 0
            if count >= limit:
                return False
            db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, str(count + 1)))
            return True

    def kill_switch(self) -> bool:
        return self.get_meta('kill_switch') == '1'

    def set_kill_switch(self, enabled: bool):
        self.set_meta('kill_switch', '1' if enabled else '0')

    def put_swaps(self, swaps: list) -> int:
        """Insert decoded swaps; (pool, block, log_index) dedupes restarts and overlaps."""
        if not swaps:
            return 0
        rows = [(s.pool.lower(), s.block, s.log_index, s.transaction_hash.lower(), s.timestamp,
                 s.sender.lower(), s.recipient.lower(), str(s.amount0), str(s.amount1),
                 str(s.sqrt_price_x96), str(s.liquidity), s.tick) for s in swaps]
        with self.lock, self._connect() as db:
            before = db.total_changes
            db.executemany('INSERT OR IGNORE INTO swaps(pool,block,log_index,tx_hash,ts,sender,recipient,'
                           'amount0,amount1,sqrt_price_x96,liquidity,tick) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            return db.total_changes - before

    def swaps(self, pool: str, max_block: int | None = None, limit=5000) -> list[dict]:
        with self.lock, self._connect() as db:
            rows = db.execute('SELECT * FROM swaps WHERE pool=? AND block<=? ORDER BY block,log_index LIMIT ?',
                              (pool.lower(), max_block if max_block is not None else 2**62, limit))
            return [{'pool': r['pool'], 'block': r['block'], 'log_index': r['log_index'],
                     'transaction_hash': r['tx_hash'], 'timestamp': r['ts'], 'sender': r['sender'],
                     'recipient': r['recipient'], 'amount0': r['amount0'], 'amount1': r['amount1'],
                     'sqrt_price_x96': r['sqrt_price_x96'], 'tick': r['tick'], 'tx_from': r['tx_from']}
                    for r in rows]

    def unresolved_transactions(self, limit=40) -> list[str]:
        with self.lock, self._connect() as db:
            return [r[0] for r in db.execute(
                'SELECT tx_hash FROM swaps WHERE tx_from IS NULL GROUP BY tx_hash ORDER BY MIN(block) LIMIT ?', (limit,))]

    def set_transaction_sender(self, tx_hash: str, sender: str):
        with self.lock, self._connect() as db:
            db.execute('UPDATE swaps SET tx_from=? WHERE tx_hash=? AND tx_from IS NULL', (sender.lower(), tx_hash.lower()))

    def put_pool(self, event: dict):
        with self.lock, self._connect() as db:
            db.execute('INSERT OR IGNORE INTO pools VALUES(?,?,?,?,?,?,?)',
                (event['address'].lower(), event['token0'], event['token1'], event['fee'],
                 event['timestamp'], event['block'], json.dumps(event, sort_keys=True)))

    def pools(self, limit=100) -> list[dict]:
        with self.lock, self._connect() as db:
            return [json.loads(r['event_json']) for r in db.execute('SELECT event_json FROM pools ORDER BY timestamp DESC LIMIT ?', (limit,))]

    def snapshot(self, pool: str, state: dict, ts: float | None = None):
        with self.lock, self._connect() as db:
            db.execute('INSERT INTO snapshots(pool,ts,state_json) VALUES(?,?,?)',
                       (pool.lower(), ts if ts is not None else time.time(), json.dumps(state, sort_keys=True, default=str)))

    def snapshots(self, pool: str, limit=2000) -> list[dict]:
        with self.lock, self._connect() as db:
            return [dict(ts=r['ts'], **json.loads(r['state_json'])) for r in db.execute(
                'SELECT ts,state_json FROM snapshots WHERE pool=? ORDER BY ts ASC LIMIT ?', (pool.lower(), limit))]

    def put_decision(self, pool: str, state: dict, model: dict, raw: dict, gate: dict, ts: float | None = None):
        now = ts if ts is not None else time.time()
        record = {'ts': now, 'pool': pool, 'state': state, 'decision': model, 'raw_answer': raw, 'gates': gate}
        with self.lock:
            with self._connect() as db:
                db.execute('INSERT INTO decisions(ts,pool,action,confidence,risk_score,state_json,model_json,gate_json) VALUES(?,?,?,?,?,?,?,?)',
                    (now, pool.lower(), gate['action'], model['confidence'], model['risk_score'],
                     json.dumps(state, default=str), json.dumps({'normalized': model, 'raw_answer': raw}, default=str), json.dumps(gate)))
            with open(self.audit, 'a', encoding='utf-8') as file:
                file.write(json.dumps(record, sort_keys=True, default=str) + '\n')

    def decisions(self, limit=80) -> list[dict]:
        with self.lock, self._connect() as db:
            rows = db.execute('SELECT * FROM decisions ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(id=r['id'], ts=r['ts'], pool=r['pool'], action=r['action'],
                confidence=r['confidence'], risk_score=r['risk_score'],
                state=json.loads(r['state_json']),
                model=json.loads(r['model_json']), gate=json.loads(r['gate_json'])) for r in rows]

    def has_position(self, pool: str) -> bool:
        with self.lock, self._connect() as db:
            return db.execute('SELECT id FROM positions WHERE pool=? LIMIT 1', (pool.lower(),)).fetchone() is not None

    def open_position(self, pool, token, size_usd, units, entry_price, ts=None, mode='PAPER'):
        with self.lock, self._connect() as db:
            db.execute('INSERT INTO positions(pool,token,opened,size_usd,units,entry_price,mode) VALUES(?,?,?,?,?,?,?)',
                       (pool.lower(), token.lower(), ts if ts is not None else time.time(),
                        size_usd, units, entry_price, mode))

    def open_positions(self):
        with self.lock, self._connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM positions WHERE closed IS NULL ORDER BY opened DESC')]

    def close_position(self, position_id, price, pnl, reason, ts=None):
        with self.lock, self._connect() as db:
            db.execute('UPDATE positions SET closed=?,exit_price=?,realized_pnl=?,exit_reason=? WHERE id=? AND closed IS NULL',
                       (ts if ts is not None else time.time(), price, pnl, reason, position_id))

    def positions(self, limit=100):
        with self.lock, self._connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM positions ORDER BY opened DESC LIMIT ?', (limit,))]

    def recent_pools_with_state(self, limit=30) -> list[dict]:
        with self.lock, self._connect() as db:
            rows = db.execute('''SELECT p.event_json, s.state_json
                FROM pools p LEFT JOIN snapshots s ON s.id=(
                SELECT id FROM snapshots WHERE pool=p.address ORDER BY ts DESC,id DESC LIMIT 1)
                ORDER BY p.timestamp DESC LIMIT ?''', (limit,))
            return [{**json.loads(r['event_json']), 'state': json.loads(r['state_json']) if r['state_json'] else {}}
                    for r in rows]

    def stats(self) -> dict:
        with self.lock, self._connect() as db:
            pool_count = db.execute('SELECT COUNT(*) FROM pools').fetchone()[0]
            snap_count = db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
            dec_count = db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
            swap_count = db.execute('SELECT COUNT(*) FROM swaps').fetchone()[0]
            buy_count = db.execute("SELECT COUNT(*) FROM positions WHERE mode='PAPER'").fetchone()[0]
            closed = db.execute('SELECT realized_pnl FROM positions WHERE closed IS NOT NULL').fetchall()
            pnl = sum(float(r[0]) for r in closed if r[0] is not None)
            wins = sum(float(r[0]) > 0 for r in closed if r[0] is not None)
            return {'pools': pool_count, 'snapshots': snap_count, 'decisions': dec_count, 'swaps': swap_count,
                    'positions': buy_count, 'closed_positions': len(closed),
                    'realized_pnl_usd': round(pnl, 2),
                    'win_rate_pct': round(100 * wins / len(closed), 1) if closed else None}

    def equity_curve(self) -> list[dict]:
        with self.lock, self._connect() as db:
            rows = db.execute('SELECT closed,realized_pnl FROM positions WHERE closed IS NOT NULL ORDER BY closed ASC,id ASC')
            total = 0.0
            points = []
            for row in rows:
                total += float(row['realized_pnl'] or 0)
                points.append({'t': row['closed'], 'value': round(total, 4)})
            return points

    def today_realized_pnl(self):
        # UTC date boundary, deterministic across hosts.
        import datetime
        day_start = datetime.datetime.combine(datetime.datetime.now(datetime.timezone.utc).date(),
            datetime.time(0), tzinfo=datetime.timezone.utc).timestamp()
        with self.lock, self._connect() as db:
            row = db.execute('SELECT COALESCE(SUM(realized_pnl),0) AS pnl FROM positions WHERE closed >= ?', (day_start,)).fetchone()
            return float(row['pnl'])
