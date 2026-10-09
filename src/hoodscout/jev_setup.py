"""Local, hidden-key JEV setup. Never sends a trade or prints a credential."""
from __future__ import annotations

from dataclasses import replace
import getpass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from dotenv import set_key

from .config import Config
from .decision_engine import DecisionEngine


def engine_from_config(cfg: Config) -> DecisionEngine:
    return DecisionEngine('jev', cfg.typesafe_api_key, cfg.jev_endpoint,
                          cfg.jev_model, timeout=cfg.jev_timeout_seconds)


def check_jev(cfg: Config) -> int:
    result = engine_from_config(cfg).check_connection()
    print(json.dumps(result, indent=2))
    return 0 if result['model_status']['connected'] else 1


def save_settings(path: Path, cfg: Config):
    """Replace .env atomically, preserving other entries and existing permissions."""
    original = path.read_bytes() if path.exists() else b''
    permissions = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    handle, name = tempfile.mkstemp(prefix='.hoodscout-jev-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(original)
        for key, value in {'TYPESAFE_API_KEY': cfg.typesafe_api_key, 'DECISION_PROVIDER': 'jev',
                           'JEV_ENDPOINT': cfg.jev_endpoint, 'JEV_MODEL': cfg.jev_model}.items():
            set_key(str(temporary), key, value, quote_mode='always')
        os.chmod(temporary, permissions)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def setup_jev(cfg: Config) -> int:
    if not Path('pyproject.toml').is_file() or not Path('src/hoodscout').is_dir():
        print('Run jev-setup from your HoodScout project folder.')
        return 1
    if not sys.stdin.isatty():
        print('Run jev-setup in an interactive terminal so the key can be entered without echo.')
        return 1
    print('JEV setup for PAPER decisions. No transactions will be sent.')
    print('This performs one small API inference, which may consume TypeSafe credits.')
    prompt = 'TypeSafe API key (hidden; Enter keeps the configured key): ' if cfg.typesafe_api_key else 'TypeSafe API key (hidden): '
    key = getpass.getpass(prompt).strip() or cfg.typesafe_api_key
    if not key:
        print('No key entered. Nothing changed. Obtain API access at https://typesafe.ai first.')
        return 1
    updated = replace(cfg, typesafe_api_key=key, decision_provider='jev')
    if check_jev(updated):
        print('Connection check failed. Existing .env was not changed.')
        return 1
    try:
        save_settings(Path('.env'), updated)
    except OSError:
        print('Connection succeeded but .env could not be saved. Check local file permissions.')
        return 1
    print('Verified JEV settings saved to local .env. Restart HoodScout to use them.')
    return 0
