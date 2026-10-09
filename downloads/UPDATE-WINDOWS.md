# HoodScout discovery and price-feed update

**Current update (swap activity, JEV credit prefilter, kill switch):** use `hoodscout-jev-launcher.zip` exactly as described next; both launchers now install the cumulative `hoodscout-astra-update.zip` and run 102 tests.

**JEV update:** use `hoodscout-jev-launcher.zip`, extract it and double-click `Connect-JEV-and-Start-HoodScout.cmd`. See `JEV-WINDOWS.txt` for the hidden-key setup and diagnostics. It includes the discovery/price fixes below plus the corrected TypeSafe API contract, connection health and 75 passing tests. The current standard launcher also installs that cumulative update and honors the provider in `.env`. The historical manual instructions below keep the stub selected; use the dedicated JEV launcher when connecting your new API key.

## Automatic download and startup

Download `Update-and-Start-HoodScout.cmd` from this folder and double-click it in Windows. Stop the previous server with Ctrl+C first. The CMD file invokes noninteractive PowerShell without loading the broken PSReadLine paste handler; no PowerShell block needs to be pasted. It locates the existing project under `%USERPROFILE%\Desktop\dev\hoodscout-robinhood` (or accepts the project folder dragged onto the CMD file), downloads the ZIP from its immutable Git commit, verifies SHA-256, extracts it into a new temporary directory, runs the protective updater and tests, then starts the PAPER server and opens the browser after the API responds. It leaves errors visible if anything fails. No administrator access or PowerShell execution-policy change is required.

The CMD launcher has not been executed on Windows from the cloud environment. Its ZIP payload/updater and Python application were validated separately as described below. Live two-source price readiness must still be checked in the dashboard.

## Manual update

1. Stop the old HoodScout server with Ctrl+C. Leave VS Code open.
2. Extract this ZIP into `Downloads/hoodscout-update` so that `apply_update.py`, `manifest.json` and `payload` are in that directory.
3. In PowerShell, from your existing project folder, run:

```powershell
& {
    .\.venv\Scripts\python.exe "$env:USERPROFILE\Downloads\hoodscout-update\apply_update.py" --project .
    if ($LASTEXITCODE -ne 0) { throw 'Update stopped; read the message above' }
    .\.venv\Scripts\python.exe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; do not start the scanner yet' }
    $env:DATA_DIR = './data/paper'
    $env:LIVE_MODE = 'false'
    $env:DECISION_PROVIDER = 'stub'
    $env:PRICE_FEED_MODE = 'coinbase_kraken'
    $env:QUOTER_V2_ADDRESS = '0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7'
    .\.venv\Scripts\hoodscout.exe serve
}
```

Refresh the dashboard already open in your browser. The ETH/USD REFERENCE strip should show Coinbase + Kraken, age and price difference, or ENTRY BLOCKED with the reason. In a second PowerShell terminal:

```powershell
$state = Invoke-RestMethod 'http://127.0.0.1:8000/api/state'
$state | Select-Object mode, scanner_online, scanner_error, pool_discovery_topic_version
$state.price_feed | ConvertTo-Json -Depth 5
```

The updater verifies original file checksums, checks all files before applying changes, preserves local edits by stopping on conflicts, and backs up originals in `hoodscout-update-backups`. It never replaces `.env`, `.venv`, or `data`. No new Python dependencies are required. Rerunning the updater is harmless when the update is already applied. Use `--check` for validation without changes.

Discovery uses a new persistent cursor and rescans only the configured recent BACKFILL_BLOCKS window once. Old databases and records are preserved. This is not a complete recovery of all historically missed launches.

The reference uses actual trade timestamps from Coinbase and Kraken. Both must be fresh (default 60 seconds), no more than 5 seconds in the future, and agree within 1 percent. It refreshes every 10 seconds; failures discard cached good prices immediately. This is an external research reference, not a Robinhood on-chain oracle. Quoter calls need observed initialized WETH pools. Missing/stale quotes, mismatched Quoter factory/WETH and price impact at or above 3 percent veto entries.

The independent data-source test still needs to pass on your Windows machine. The cloud proxy blocks live exchange requests, which was confirmed to leave the updated application without a usable USD reference and without entries. The default decision stub remains HOLD; this update does not enable automated buys or implement the Jev integration. Live transactions remain disabled.

Validation: 51 tests passed on Python 3.12 in the cloud, including an independently captured Robinhood PoolCreated event at block 9490, stale/future/disagreeing prices, invalid responses, cached-price expiry, failed quotes, quote expiry after model latency and repeated rejection of a mismatched Quoter. Dashboard/API startup was checked with a working mainnet RPC and blocked price feed. A real Quoter eth_call was verified previously. No live trade was sent. Windows execution itself must still be checked in your session.
