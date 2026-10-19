# HoodScout: Gemini paper setup

1. Get a Gemini Developer API key at https://aistudio.google.com/apikey. Select/import the
   Google Cloud project linked to your billing account. Verify that your promotional credit
   is eligible for the service; an existing balance or Gemini Pro subscription does not prove
   API coverage. This adapter is for Gemini Developer API, not Vertex AI.
2. Open your existing project `.env` in VS Code. Add `GEMINI_API_KEY=your-key` locally.
   Optional: `GEMINI_MODEL=gemini-2.5-flash-lite`, `GEMINI_MAX_CALLS_PER_DAY=100`.
   Model availability is checked on your account; choose an available Gemini model if rejected.
   Do not share the key, overwrite your .env or change LIVE_MODE to true.
3. Stop the old HoodScout server with Ctrl+C. Download and **extract the entire ZIP**.
4. Double-click `Update-and-Start-Gemini.cmd`. Default project:
   `%USERPROFILE%\Desktop\dev\hoodscout-robinhood`. For another path, drag that folder onto the CMD.
5. The updater validates known source hashes, preserves .env/data/.venv, backs up changed files,
   runs regression tests, verifies one real synthetic Gemini response, and selects Gemini only
   after success. It then starts the paper dashboard and opens your browser. A server startup
   probe can use a second API call. Keep the terminal and PC running.

The launcher uses the existing `data/paper` ledger and clears inherited provider/key overrides
so Gemini credentials come from your local .env. It makes no changes to your other Google project
or its credentials. Google quota/spending can still be shared across applications in one project.
The request cap is persistent per database, UTC day, including failed calls; it is not a dollar cap.

If Google returns 400/403, check API enablement, key restrictions, billing and regional availability.
404 can mean the configured model is unavailable. 429 indicates quota/rate limits. No automatic
retry or fallback spends extra credits. Share only the sanitized check result, never your key.

Paper exits now require fresh reverse QuoterV2 quotes. Unquotable positions remain open instead
of recording spot-price profits. These quotes are not token-transfer/router simulations and
still omit gas/MEV. Existing closed ledger records and historical replay are unchanged.

Live transactions remain unavailable. This package does not install a wallet signer or solve
order/nonce reconciliation. A dedicated Robinhood-compatible wallet, explicit capital/loss limits
and verified, tested router/signing integration are still required before any live pilot.
No Phantom seed phrase or private key belongs in this setup.
