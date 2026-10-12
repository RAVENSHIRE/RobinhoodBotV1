"""JEV setup that reads the key from the project .env. Never sends a trade or prints a credential."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import httpx
from dotenv import set_key

from .config import Config, API_KEY_NAMES, api_key_from_values, clean_api_key, read_env_file
from .decision_engine import DecisionEngine

MODELS_URL = 'https://api.typesafe.ai/v1/models'
MISSING_KEY = 3


def engine_from_config(cfg: Config) -> DecisionEngine:
    return DecisionEngine('jev', cfg.typesafe_api_key, cfg.jev_endpoint,
                          cfg.jev_model, timeout=cfg.jev_timeout_seconds)


def inspect_env_key(path: Path) -> tuple[dict, str | None]:
    """Find the key in .env and describe its format without revealing any of it."""
    values, encoding, text = read_env_file(path)
    key, name = api_key_from_values(values)
    entries = sum(1 for line in text.splitlines()
                  if line.strip().split('=', 1)[0].removeprefix('export ').strip() in API_KEY_NAMES)
    raw = values.get(name) if name else None
    issues = []
    if raw is not None and raw.strip() != key:
        issues.append('quotes_spaces_or_bearer_prefix_removed')
    if key and (not key.isascii() or not key.isprintable() or ' ' in key):
        issues.append('key_contains_spaces_or_non_ascii_characters')
    if entries > 1:
        issues.append('multiple_key_lines_first_valid_one_used')
    if encoding in ('utf-16', 'utf-8-bom', 'cp1252'):
        issues.append(f'env_file_encoding_{encoding}_converted_on_save')
    return {'env_file': str(path), 'env_file_found': path.is_file(), 'key_found': bool(key),
            'key_variable': name, 'key_length': len(key) if key else 0,
            'notes': issues}, key


def probe_models(key: str, client_factory=httpx.Client) -> int | str:
    """GET /v1/models (no inference, no credits) to tell a bad key from missing access."""
    try:
        with client_factory(timeout=15, follow_redirects=False) as client:
            return client.get(MODELS_URL, headers={'Authorization': f'Bearer {key}'}).status_code
    except httpx.TimeoutException:
        return 'timeout'
    except httpx.HTTPError:
        return 'network_error'


def explain(status: str, models_status) -> str:
    if status == 'authentication_failed':
        if models_status == 200:
            return ('TypeSafe accepts this key for listing models but rejected the JEV request (401). '
                    'Check in your TypeSafe account that the key has System One / JEV API access.')
        return ('TypeSafe rejected this key (HTTP 401). Create a new API key in your TypeSafe account, '
                'copy the whole key, replace the value after TYPESAFE_API_KEY= in .env, save, and run this again.')
    if status == 'payment_required':
        return 'The key works but the account needs credits or billing (HTTP 402).'
    if status == 'access_denied':
        return 'TypeSafe denied access (HTTP 403). Check account/API access, VPN or firewall.'
    if status in ('network_error', 'timeout'):
        return 'api.typesafe.ai could not be reached. Check the internet connection, VPN, proxy or firewall.'
    return 'JEV did not return a valid answer. See status above.'


def check_jev(cfg: Config, client_factory=httpx.Client) -> int:
    engine = engine_from_config(cfg)
    if client_factory is not httpx.Client:
        engine.client_factory = client_factory
    result = engine.check_connection()
    status = result['model_status']['status']
    if not result['model_status']['connected'] and cfg.typesafe_api_key and status in (
            'authentication_failed', 'access_denied'):
        result['models_endpoint_http_status'] = probe_models(cfg.typesafe_api_key, client_factory)
    print(json.dumps(result, indent=2))
    if result['model_status']['connected']:
        print('JEV OK: a real JEV answer was received and validated.')
        return 0
    print('JEV NOT CONNECTED: ' + explain(status, result.get('models_endpoint_http_status')))
    return 1


def _atomic_write(path: Path, text: str, updates: dict[str, str] | None = None):
    """Write UTF-8 .env text (plus key updates) atomically, keeping file permissions."""
    permissions = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    handle, name = tempfile.mkstemp(prefix='.hoodscout-jev-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
        for key, value in (updates or {}).items():
            set_key(str(temporary), key, value, quote_mode='always')
        os.chmod(temporary, permissions)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_settings(path: Path, cfg: Config):
    """Enable JEV in .env, preserving other entries; converts UTF-16/BOM files to UTF-8."""
    _, _, text = read_env_file(path)
    _atomic_write(path, text, {'TYPESAFE_API_KEY': cfg.typesafe_api_key, 'DECISION_PROVIDER': 'jev',
                               'JEV_ENDPOINT': cfg.jev_endpoint, 'JEV_MODEL': cfg.jev_model})


def add_key_placeholder(path: Path):
    _, _, text = read_env_file(path)
    if any(line.strip().startswith('TYPESAFE_API_KEY') for line in text.splitlines()):
        return
    if text and not text.endswith('\n'):
        text += '\n'
    _atomic_write(path, text + 'TYPESAFE_API_KEY=\n')


def open_in_editor(path: Path) -> bool:
    if sys.platform != 'win32':
        return False
    # Blocks until Notepad is closed. Pasting with Ctrl+V works normally there.
    subprocess.run(['notepad.exe', str(path)], check=False)
    return True


def setup_jev(cfg: Config, env_path: Path = Path('.env'), editor=open_in_editor,
              client_factory=httpx.Client) -> int:
    if not Path('pyproject.toml').is_file() or not Path('src/hoodscout').is_dir():
        print('Run jev-setup from your HoodScout project folder.')
        return 1
    print('JEV setup for PAPER decisions. No transactions will be sent.')
    report, key = inspect_env_key(env_path)
    if not key:
        add_key_placeholder(env_path)
        print(f'No TypeSafe API key found in {env_path.resolve()}.')
        print('Paste your key directly after TYPESAFE_API_KEY= (no spaces or quotes), save the file and close the editor.')
        if editor(env_path):
            report, key = inspect_env_key(env_path)
    if not key:
        print('Still no key in .env. Open .env in VS Code, put your key after TYPESAFE_API_KEY=, save, and run this again.')
        return MISSING_KEY
    print('Key source: ' + json.dumps(report))
    print('Sending one small synthetic JEV request (may use a few TypeSafe credits)...')
    updated = replace(cfg, typesafe_api_key=clean_api_key(key), decision_provider='jev')
    if check_jev(updated, client_factory):
        print('.env was not changed.')
        return 1
    try:
        save_settings(env_path, updated)
    except OSError:
        print('JEV works, but .env could not be updated. Set DECISION_PROVIDER=jev in .env yourself.')
        return 1
    print('JEV enabled in .env (DECISION_PROVIDER=jev). Start or restart HoodScout to use it.')
    return 0
