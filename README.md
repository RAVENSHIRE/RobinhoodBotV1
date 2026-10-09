# HOODSCOUT / ROBINHOOD CHAIN

**Standalone, Robinhood Chain-only** Uniswap V3 pool scanner, Jev-compatible decision bridge, independent risk gates, paper-trading ledger, research replay, and monochrome dashboard. **Not related to or built into the existing Solana bot.**

**Status:** research MVP; live order broadcasting intentionally unimplemented / blocked. Read-only chain ID, contract getters and a QuoterV2 call were verified on Robinhood mainnet on 2026-10-09. No profitable strategy or live trading readiness is claimed. Sample dashboard data is **synthetic**, clearly labeled, and isolated in `data/demo`.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env                  # only if .env does not already exist; configure QUOTER_V2_ADDRESS
hoodscout serve
# open http://127.0.0.1:8000
```

Python 3.11+ required. For a dashboard with *no* network calls (empty local ledger):

```bash
hoodscout serve --no-scan
```

For a populated, **explicitly synthetic** visual preview:

```bash
hoodscout demo
DATA_DIR=./data/demo hoodscout serve --no-scan
```

Scanner-only CLI: `hoodscout scan`. Run historical replay analyses: `hoodscout backtest --entry-second 60 --hold-seconds 300`.

## Data flow

```text
Robinhood Chain JSON-RPC 4663
         │
         ▼
Uniswap V3 Factory PoolCreated (block-aware polling, persistent cursor)
         │
         ▼
ERC-20 contract state + slot0 + pool balances (WETH pairs)
         │
         ▼
QuoterV2 eth_call (optional, verified address required)
         │
         ▼
Snapshots → external Jev-compatible decision → hard risk gates
         │                              │
         └── SQLite / JSONL audit      └── PAPER ledger (only when all gates pass)
                                                  │
                                                  ▼
                                       Web dashboard + replay API
```

The startup RPC check fails closed if the endpoint doesn't report Robinhood mainnet chain ID `4663`. Factory scans are bounded by `MAX_BLOCK_RANGE` to accommodate rate-limited RPCs and persist the last successful block. Event timestamps are derived from blocks; the first scan defaults to the latest `BACKFILL_BLOCKS` blocks, so this is **not full historic indexing**.

## Main contracts / references

- Network: `https://rpc.mainnet.chain.robinhood.com`; chain ID `4663`; native gas ETH.
- Uniswap V3 Factory: `0x1f7d7550B1b028f7571E69A784071F0205FD2EfA`.
- WETH: `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73`.
- **QUOTER_V2_ADDRESS must be verified and configured**. Independently documented candidate `0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7`. A non-empty code check alone doesn't prove the quoter is authentic; verify source and its factory before trading.
- References: [Robinhood official network docs](https://docs.robinhood.com/chain/connecting/); [third-party contract registry](https://docs.hoodl.app/robinhood-chain/).

**IMPORTANT about liquidity:** Spot price is read from Uniswap V3 `slot0`, properly scaled for ERC-20 decimals. Pool token balances can derive an approximate USD TVL **only for WETH pairs when the timestamped Coinbase/Kraken ETH/USD reference passes validation**. This is *not executable depth*. The risk gates additionally demand a successful QuoterV2 quote for the intended paper order and price impact under the configured threshold. `liquidity_usd` in snapshot states is a TVL proxy and only filled after such a quote; it is NOT the value one can necessarily withdraw or sell. A trustworthy live ETH/USD oracle, high-quality fill simulation and verification of WETH reserve reliability are needed before relying on any result. No actual stable-coin/non-WETH USD valuations are made.

**IMPORTANT about ownership:** `owner()` is optional and a nonstandard assumption for many ERC-20s. If absent, ownership is `null`, not `false`. Default gates reject unknown or non-renounced contracts. Even renouncement does NOT mean scam-free or sellable. There is not yet a token honeypot detector.

## Jev decision model

Default `DECISION_PROVIDER=stub`: returns **HOLD** and risk score 10 for every observation. Therefore the scanner can discover pools, observe prices and log decisions but **cannot generate fake buys without a model**.

To enable the supplied third-party Jev adapter:

```dotenv
DECISION_PROVIDER=jev
TYPESAFE_API_KEY=your_secret_key
```

The adapter sends `state`, `questions` and `model=jev-latest` to `https://api.typesafe.ai/v1/systemone` and normalizes `action`, `confidence`, and `risk_score`. Its exact production API contract has **not** been verified in this environment. Malformed/error responses fail closed to HOLD. The dashboard never exposes the key.

The hard gates operate **after** the model and independently check min TVL proxy ($20K), verified fresh quoter execution quote, strictly less than 3% quote impact, observed 1–100% trailing volatility, min confidence 0.70, max risk score 4, renounced ownership and real bytecode. Also, `LIVE_MODE=true` is **not sufficient to trade**: the live executor refuses to send any transaction in this release. No real private keys required or accepted.

## Research replay

Snapshots are recorded **when observed**; the bot does not fabricate historical ticks. For a requested entry at T+60 seconds and holding 300 seconds, the replay requires actual time-appropriate snapshots at the entry and exit. It omits incomplete tokens, reporting how many were usable. Simple replay net return includes an explicit fee/slippage haircut on each side but not chain gas, latency, MEV, taxes, or guaranteed historical fillability. Use only as preliminary research.

API endpoints:

- `GET /api/state`: full dashboard state (incl. paper ledger, decisions, recent launch feed).
- `GET /api/stream`: server-sent event updates.
- `GET /api/replay?pool=0x...&entry_second=60&hold_seconds=300`.
- `GET /api/backtest?entry_second=60&hold_seconds=300`.

Persisted files under `DATA_DIR`:

- `hoodscout.db` — SQLite pool events, snapshots, decisions, paper positions and persistent cursor.
- `decisions.jsonl` — append-only normalized+raw model audit records with full states.

## Operational limitations (read before using)

1. **Read-only scanning + paper mode.** Live broadcasting is deliberately unavailable; this project is not a functioning live sniper.
2. **Launch detection is pool creation, not token contract deployment**; a pool can appear uninitialized or receive liquidity later.
3. **No measured unique buyer count yet.** That requires dedicated Swap event decoding, transaction sender tracking and wallet classification, which is a future module. `buy_count` and `sell_count` are `null` instead of fabricated.
4. **No historical backfill of months of tokens.** Start on the live stream; restore limited recent pool state from SQLite after restart.
5. **Research price reference, not an on-chain oracle.** Coinbase and Kraken last-trade prices are refreshed and cross-checked. Both must be fresh. Exchange prices do not prove bridged WETH redeemability, chain-local USD value or executable sell prices. Static `ETH_USD` is ignored by the scanner.
6. **A quote is not sellability proof.** The current MVP does not simulate a complete buy/approve/sell round trip or handle fee-on-transfer tokens.
7. **Jev remains unverified.** Mainnet RPC/getters and a real QuoterV2 call were checked; the sampled pool exceeded the price-impact limit. Coinbase was checked on Windows. The cloud proxy currently blocks exchange-feed requests, so live combined Coinbase/Kraken operation still requires validation in the target environment.
8. **Default binding is localhost.** Never expose the unauthenticated dashboard or API publicly without access control.
9. **Research stop/take-profit is spot-derived**, not an executable sell quote, and excludes gas/MEV. Paper P&L may overstate attainable results.
10. The current scanner has no optimized throughput for extremely high launch rates; use an indexed data source/robust provider before scaling.

## Tests

```bash
PYTHONPATH=src pytest -q
```

## UX

Monochrome interface inspired by the supplied screenshot: left session P&L, KPI header and equity history, right activity log, and five bottom status modules (SCOUT, LEDGER, PULSE, FLUX, WARDEN). Demo data cannot be mistaken for real P&L: `SYNTHETIC DEMO` is displayed in the header.


## Price-feed and discovery update

The scanner uses the canonical Uniswap V3 `PoolCreated` topic. Older builds used an incorrect topic; an online scanner did not prove that any launches were discovered. On first start after this update a new `pool_created_cursor_v2` starts a bounded `BACKFILL_BLOCKS` window. Subsequent starts resume that cursor. Existing events, paper positions, decisions and databases are preserved. This does not recover all earlier missed launches. Increase `BACKFILL_BLOCKS` before the first updated run if a larger recovery window is required; do not delete databases to reset a cursor. RPC queries remain bounded by `MAX_BLOCK_RANGE`.

Default price settings (no API keys):

```dotenv
PRICE_FEED_MODE=coinbase_kraken
PRICE_REFRESH_SECONDS=10
PRICE_MAX_AGE_SECONDS=60
PRICE_MAX_DIVERGENCE_PCT=1
PRICE_FUTURE_TOLERANCE_SECONDS=5
QUOTE_MAX_AGE_SECONDS=15
```

Coinbase Exchange `GET /products/ETH-USD/ticker` supplies the primary USD last-trade price. Kraken `GET /0/public/Trades?pair=ETHUSD&count=1` supplies an independent last trade. The oldest trade timestamp controls freshness. Symmetric percentage disagreement uses the mean of both prices as denominator. Missing/malformed data, HTTP failures, nonfinite values, future timestamps and stale trades invalidate the reference. There is no static or single-source fallback. A failed refresh immediately discards the prior price; cached data also expires between refreshes. `PRICE_FEED_MODE=off` disables USD valuations and entry eligibility.

Snapshots and JSONL decisions record both sources, their timestamps, reference price and disagreement. Risk gates recheck reference and quote age after the model responds. A quote at or above the price-impact limit is rejected. USD paper marks are withheld when the reference expires; existing positions remain open until usable observations return. Spot-derived exits still have the limitations listed above.

Network access is needed for `rpc.mainnet.chain.robinhood.com`, `api.exchange.coinbase.com` and `api.kraken.com`. The dashboard exposes `price_feed` in `/api/state` and shows either the cross-checked price/ages or the blocking reason. Neither a configured key nor an open HTTP port proves the model/scanner/feed works.

For Windows, keep the existing `.env`, stop the old server with Ctrl+C, then from the project PowerShell terminal run:

```powershell
$env:DATA_DIR = './data/paper'
$env:LIVE_MODE = 'false'
$env:DECISION_PROVIDER = 'stub'
$env:PRICE_FEED_MODE = 'coinbase_kraken'
$env:QUOTER_V2_ADDRESS = '0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\hoodscout.exe serve
```

The Quoter address matches Uniswap's sdk-core Robinhood registry and returned the configured factory/WETH and a valid quote at block 84278943. This is functional evidence, not a bytecode audit or token sellability proof. Each running monitor checks its factory/WETH before quoting. Previously rejected Quoters remain rejected. Quotes are requested only for observed initialized WETH pools with validated reference prices.

Keep the stub until a decision provider is separately validated. This update does not fabricate buy activity, implement Swap tracking or automatically enable entries. Live broadcasting remains unavailable.
