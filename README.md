# HOODSCOUT / ROBINHOOD CHAIN

**Standalone, Robinhood Chain-only** Uniswap V3 pool scanner, JEV and Gemini decision adapters, independent risk gates, paper-trading ledger, research replay, and monochrome dashboard. **Not related to or built into the existing Solana bot.**

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

The adapter sends `state`, `questions` and `model=jev-latest` to `https://api.typesafe.ai/v1/systemone` using the contract in the official TypeSafe SDK 0.7.4. Score questions require an ordered `criteria` list. JEV scores that list from 0 to 9; HoodScout converts it to risk 1 to 10. The lower of action confidence and risk confidence is used by the confidence gate. Malformed/error responses fail closed to HOLD. Request/response fixtures were checked against the SDK OpenAPI models, but a real authenticated call must still pass on your machine. The dashboard never exposes the key.

The hard gates operate **after** the model and independently check min TVL proxy ($20K), verified fresh quoter execution quote, strictly less than 3% quote impact, observed 1–100% trailing volatility, at least 3 unique buyers, top buyer at most 50% of buy volume, swap data no older than 30 seconds, min confidence 0.70, max risk score 4, renounced ownership and real bytecode. Also, `LIVE_MODE=true` is **not sufficient to trade**: the live executor refuses to send any transaction in this release. No real private keys required or accepted.

## Gemini on Windows

Gemini is an alternative provider; it does not repair or require TypeSafe authentication.
Get a **Gemini Developer API key** at https://aistudio.google.com/apikey. In AI Studio,
select/import the Google Cloud project linked to your intended billing account. A Gemini
app/Google AI Pro subscription does not include Gemini API usage. Google Cloud promotional
credits have service restrictions: check their terms and the project's billing report before
assuming Gemini Developer API calls are covered. This adapter does not use Vertex AI/ADC.
For an existing shared project, use a separate API key with the appropriate API restriction;
this separates credentials but does not isolate the project's quota or spending.

Add these settings to your **existing local `.env`** (never commit or send the real key):

```dotenv
GEMINI_API_KEY=your_local_key
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_MAX_CALLS_PER_DAY=100
LIVE_MODE=false
```

The model ID is configurable. Its availability for your account is established by the check,
not assumed from this example. Stop the old server and run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m hoodscout.cli gemini-setup
.\.venv\Scripts\python.exe -m hoodscout.cli serve
```

`gemini-setup` uses the configured key without a paste prompt, makes one small synthetic
inference (potentially billable), and selects `DECISION_PROVIDER=gemini` only after a valid
HOLD response. `gemini-check` checks without editing `.env`. Normal process environment
variables take precedence over `.env`; remove an old `DECISION_PROVIDER=stub` override when
starting manually. No key is required in this cloud development environment.

The model receives a bounded allowlist of measurements and returns an entry case and a
counter-case in **one request**. These are two perspectives from one model, not independent
agents or a calibrated trading edge. Gemini always passes deterministic prefiltering first,
even if `JEV_CALL_POLICY=all`. Its separate `GEMINI_MIN_CONFIDENCE=0.85` and
`GEMINI_MAX_RISK_SCORE=3` are uncalibrated paper-research settings, not reused JEV calibration
or probabilities of profit. Validate them on held-out observations before any live use.

The daily attempt counter is persisted in the selected `DATA_DIR` and resets at UTC midnight.
It counts startup/check requests and failed calls, survives restarts and reserves attempts
atomically across processes. It is a **call cap, not a currency budget**; another database,
another application or a changed cap has its own usage. Output is capped at 512 tokens and
observation input at 12 KB. Google billing budget alerts generally do not stop spending.
There are no automatic model retries. HTTP errors, timeouts, blocked/truncated/malformed
answers, exhausted attempts and missing keys all produce HOLD. The dashboard reports the
actual provider, connection result and call count, never key values.

## Paper exits and the remaining live integration

Open positions now require a fresh **token-to-WETH QuoterV2 quote for their held quantity**
and a fresh USD reference before a paper stop, take-profit or maximum-hold exit is recorded.
Quotes are bound to the pool, token and position quantity. A missing/reverted/stale quote
leaves the position open, even after its maximum holding time; a spot price cannot fabricate
a fill. Exit checks do not depend on AI availability or the entry kill switch. Legacy recorded
closed trades and replay results are preserved; replay remains a spot-based research estimate.

QuoterV2 does not establish transferability, approvals, actual router execution, gas costs or
the ability to sell a taxed/blocked token. These paper exits still exclude gas/MEV and are
not real fills. Snapshot `exit_quotes` and `exit_quote_error` explain observed exit availability.

**Live trading is still unavailable.** The remaining implementation requires a verified router
and transaction simulation, dedicated wallet signing, bounded approvals, persistent order/
nonce/receipt reconciliation (including restart and reorg handling), and monitoring independent
of model outages. A funded wallet's public address and explicit order/exposure/daily-loss
limits are needed before an actual live pilot. Phantom availability alone does not establish
Robinhood Chain support. Do not export your existing Phantom recovery phrase into HoodScout.
No signer, wallet key, automatic transfer or broadcast is included in this update.

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
3. **Swap activity covers watched pools only.** Buyers/sellers come from decoded Uniswap V3 Swap events of pools in the active watch list, identified by transaction sender. There is no wallet clustering, bot labelling or holder-balance indexing; `holder_concentration` stays `null`.
4. **No historical backfill of months of tokens.** Start on the live stream; restore limited recent pool state from SQLite after restart.
5. **Research price reference, not an on-chain oracle.** Coinbase and Kraken last-trade prices are refreshed and cross-checked. Both must be fresh. Exchange prices do not prove bridged WETH redeemability, chain-local USD value or executable sell prices. Static `ETH_USD` is ignored by the scanner.
6. **A quote is not sellability proof.** The current MVP does not simulate a complete buy/approve/sell round trip or handle fee-on-transfer tokens.
7. **Jev remains unverified.** Mainnet RPC/getters and a real QuoterV2 call were checked; the sampled pool exceeded the price-impact limit. Coinbase was checked on Windows. The cloud proxy currently blocks exchange-feed requests, so live combined Coinbase/Kraken operation still requires validation in the target environment.
8. **Default binding is localhost.** Never expose the unauthenticated dashboard or API publicly without access control.
9. **Paper stop/take-profit now requires a reverse quote**, but still excludes gas/MEV and token-transfer simulation. Historical replay remains spot-derived. Paper P&L may overstate attainable results.
10. The current scanner has no optimized throughput for extremely high launch rates; use an indexed data source/robust provider before scaling.

## Swap activity tracking

For every watched WETH pool the scanner reads Uniswap V3 `Swap` events (`0xc42079f9…ca67`) in the same bounded block chunks as pool discovery, so a pool discovered by the scanner has swap history from its creation block. Swaps are stored in SQLite and deduplicated by pool, block and log index, which makes restarts and overlapping reads harmless. The transaction sender of each swap (`eth_getTransactionByHash`) identifies the trader, because the `sender` field of a Swap is usually a router. Lookups are bounded by `MAX_TX_LOOKUPS_PER_CYCLE` and catch up over later cycles.

Each snapshot records, using only swaps from blocks the scanner had fully read by then: buy/sell counts, WETH and USD buy/sell volume, net flow, unique buyers/sellers/traders, swaps per minute, peak swaps per second, buys and unique buyers in the last 30 seconds versus the 30 seconds before (buyer acceleration), top and top-3 buyer share of buy volume, wallets that both bought and sold, same-block round trips, and the 60-second realized price range from post-swap prices. When complete swap history exists, that swap-based range replaces the coarser snapshot-based `volatility_pct` (`volatility_source` says which one was used).

Wallet metrics are withheld (`null`) while any affected swap still has an unresolved sender, and concentration and swap-based volatility require complete history since pool creation. `activity_lag_seconds` measures how far the recorded swaps trail the observation. The gates reject unknown, stale or concentrated activity: `MIN_UNIQUE_BUYERS=3`, `MAX_TOP_BUYER_SHARE_PCT=50`, `ACTIVITY_MAX_LAG_SECONDS=30`. Each extra watched pool adds no extra log request; each new transaction adds one sender lookup.

## JEV credits, kill switch and replay statistics

`JEV_CALL_POLICY=prefiltered` (default) sends a pool to JEV only when every non-model gate would already approve the strongest possible model answer. If a deterministic gate fails, no model answer could approve an entry, so no credits are spent; the audit record then shows `sent: false` and the blocking reasons. `JEV_CALL_POLICY=all` restores one call per 30-second decision window for research.

The dashboard **KILL SWITCH** button (or `POST /api/kill-switch` with `{"enabled": true}` and header `X-HoodScout-Control: 1`) immediately blocks JEV calls and every new paper entry, and survives restarts because it is stored in the database. Existing paper positions continue to be marked and closed. When `HOST` is a loopback address the server rejects requests whose `Host` header is not `127.0.0.1`/`localhost`, which stops DNS-rebinding pages from using the local API.

Replay/backtest summaries now report expectancy per trade, total cost haircut, maximum drawdown of the cumulative net P&L, exclusions by reason, and an in-sample versus chronological-holdout split (latest 30% of usable trades). No parameters are fitted, so the holdout is only a stability check.

## Tests

```bash
PYTHONPATH=src pytest -q
```

GitHub Actions (`.github/workflows/ci.yml`) runs the suite on Linux and Windows with Python 3.11–3.13. The tests use only synthetic fixtures and mocks; they never contact the chain, exchanges or JEV.

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


## JEV setup and connection diagnostics

Obtain an API key from your own TypeSafe account at https://typesafe.ai. API inference may require account credits. Do not paste keys into chat, GitHub or command-line arguments.

From the project directory, run `.venv\Scripts\python.exe -m hoodscout.cli jev-setup` on Windows (or `.venv/bin/python -m hoodscout.cli jev-setup` on Linux). It asks for the key without echo, performs one small explicitly synthetic inference with no executor, and saves the verified settings to local `.env` only after a valid API response. Other `.env` entries are preserved and the replacement is atomic. Failed checks leave the file unchanged. The key stays in your local ignored `.env`. Restart HoodScout afterward. No wallet key is used.

`hoodscout jev-check` makes the same no-trade connection check and returns a nonzero exit code on failure. It reports safe status codes for missing keys, 401 authentication failures, 402 credits/billing, 403 access, invalid request/model, rate limits, network errors, timeouts and invalid responses. It does not print credentials or raw error bodies. There are no automatic API retries that could multiply billable checks.

When the real scanner starts with `DECISION_PROVIDER=jev`, it performs one synthetic connection check. Real pool observations are sent only during the existing decision windows. Each API inference may consume credits. All existing independent liquidity, price, quote-age and risk gates remain active after a model BUY. The connectivity check itself never opens a position, regardless of the model's answer. Swap/buyer tracking is described below; wallet clustering and holder indexing are not implemented.

`/api/state` now includes `model_status`. `model_connected` becomes true only after an actual response is validated, not merely because a key exists. It clears after an error or when the most recent successful check is older than five minutes; that age limit does not assert the service is down and a subsequent request checks it again. Model health is in-process and must be rechecked after restart. `serve --no-scan` does not call JEV automatically.

The standard Windows launcher honors `.env` instead of overriding it with STUB. The dedicated JEV launcher performs the update, local hidden-key setup, check and restart. Avoid starting old launcher copies that still force STUB. Set `JEV_TIMEOUT_SECONDS` to adjust the request timeout; slow responses may expire the separate quote-age gate, which remains mandatory.

Contract reference: https://github.com/typesafe-ai/typesafe-sdk-python and https://pypi.org/project/typesafe-sdk/0.7.4/ . Verified wheel SHA-256: `61cf3f2fe9da20b68c3d3fe2fb76bbb2f35f8c4a9a65a6b81c43a535d2a53210`. This project uses its existing httpx dependency; installing the SDK is not required.
