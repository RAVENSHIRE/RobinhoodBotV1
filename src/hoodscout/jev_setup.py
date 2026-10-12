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

from .config import Config, JEV_ROUTES, read_env_file, resolve_jev_route
from .decision_engine import DecisionEngine

MODELS_URL = 'https://api.typesafe.ai/v1/models'
MISSING_KEY = 3


def engine_from_config(cfg: Config) -> DecisionEngine:
    return DecisionEngine('jev', cfg.typesafe_api_key, cfg.jev_endpoint,
                          cfg.jev_model, timeout=cfg.jev_timeout_seconds, route=cfg.jev_route)


def inspect_env_key(path: Path, route_setting: str = 'auto', environ=None) -> tuple[dict, dict]:
    """Pick the route and key and describe the key's format without revealing any of it."""
    values, encoding, text = read_env_file(path)
    info = resolve_jev_route(route_setting, text, os.environ if environ is None else environ)
    key, name, entries = info['key'], info['key_variable'], info['key_lines']
    raw = values.get(name) if name and info['key_source'] == '.env' else None
    issues = []
    if raw is not None and raw.strip() and raw.strip() != key:
        issues.append('quotes_spaces_or_bearer_prefix_removed')
    if key and (not key.isascii() or not key.isprintable() or ' ' in key):
        issues.append('key_contains_spaces_or_non_ascii_characters')
    if entries > 1:
        issues.append('several_key_lines_last_filled_one_used')
    if encoding in ('utf-16', 'utf-8-bom', 'cp1252'):
        issues.append(f'env_file_encoding_{encoding}_converted_on_save')
    return {'route': info['route'], 'service': info['service'], 'endpoint': info['endpoint'],
            'env_file': str(path), 'env_file_found': path.is_file(), 'key_found': bool(key),
            'key_variable': name, 'key_source': info['key_source'], 'key_length': len(key) if key else 0,
            'notes': issues}, info


def probe_models(key: str, client_factory=httpx.Client) -> int | str:
    """GET /v1/models (no inference, no credits) to tell a bad key from missing access."""
    try:
        with client_factory(timeout=15, follow_redirects=False) as client:
            return client.get(MODELS_URL, headers={'Authorization': f'Bearer {key}'}).status_code
    except httpx.TimeoutException:
        return 'timeout'
    except httpx.HTTPError:
        return 'network_error'


def explain(status: str, models_status, route: str = 'typesafe') -> str:
    if route == 'vercel':
        return {
            'authentication_failed': 'Vercel AI Gateway rejected this key (HTTP 401). In the Vercel dashboard open '
                'AI Gateway > API Keys, create a key, put it after VERCEL_TYPESAFE_API_KEY= in .env, save, and run this again.',
            'payment_required': 'Vercel AI Gateway needs billing or credits for this request (HTTP 402). '
                'Check AI Gateway billing for your Vercel team.',
            'access_denied': 'Vercel denied access (HTTP 403). Check that AI Gateway and the Jev model are available '
                'to your Vercel team; Vercel may require a payment method on file.',
            'request_rejected': 'Vercel rejected the request or model name (HTTP 400/404/422). '
                'The model is set by VERCEL_JEV_MODEL (default typesafe-ai/jev).',
            'network_error': 'ai-gateway.vercel.sh could not be reached. Check the internet connection, VPN, proxy or firewall.',
            'timeout': 'ai-gateway.vercel.sh did not answer in time. Try again or raise JEV_TIMEOUT_SECONDS.',
        }.get(status, 'JEV did not return a valid answer. See status above.')
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
    if (cfg.jev_route == 'typesafe' and not result['model_status']['connected']
            and cfg.typesafe_api_key and status in ('authentication_failed', 'access_denied')):
        result['models_endpoint_http_status'] = probe_models(cfg.typesafe_api_key, client_factory)
    print(json.dumps(result, indent=2))
    if result['model_status']['connected']:
        print('JEV OK: a real JEV answer was received and validated.')
        return 0
    print('JEV NOT CONNECTED: ' + explain(status, result.get('models_endpoint_http_status'), cfg.jev_route))
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


def save_settings(path: Path, cfg: Config, key_variable: str | None = None):
    """Enable JEV and pin its route in .env, preserving other entries (UTF-16/BOM become UTF-8).

    The key is rewritten (cleaned of quotes or a Bearer prefix) only under ``key_variable``.
    """
    _, _, text = read_env_file(path)
    updates = {'DECISION_PROVIDER': 'jev', 'JEV_ROUTE': cfg.jev_route}
    if key_variable:
        updates[key_variable] = cfg.typesafe_api_key
    _atomic_write(path, text, updates)


def add_key_placeholder(path: Path, variable: str):
    _, _, text = read_env_file(path)
    if any(line.strip().startswith(variable) for line in text.splitlines()):
        return
    if text and not text.endswith('\n'):
        text += '\n'
    _atomic_write(path, text + f'{variable}=\n')


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
    report, info = inspect_env_key(env_path, cfg.jev_route_setting)
    if not info['key']:
        variable = JEV_ROUTES[info['route']]['keys'][0]
        add_key_placeholder(env_path, variable)
        print(f"No {info['service']} key found in {env_path.resolve()}.")
        print(f'Paste your key directly after {variable}= (no spaces or quotes), save the file and close the editor.')
        if editor(env_path):
            report, info = inspect_env_key(env_path, cfg.jev_route_setting)
    if not info['key']:
        variable = JEV_ROUTES[info['route']]['keys'][0]
        print(f'Still no key. Open .env in VS Code, put your key after {variable}=, save, and run this again.')
        return MISSING_KEY
    print('Key source: ' + json.dumps(report))
    print(f"Sending one small synthetic JEV request via {info['service']} (may use a little credit)...")
    model = cfg.jev_model if cfg.jev_route == info['route'] else info['model']
    updated = replace(cfg, typesafe_api_key=info['key'], decision_provider='jev', jev_route=info['route'],
                      jev_endpoint=info['endpoint'], jev_model=model)
    if check_jev(updated, client_factory):
        print('.env was not changed.')
        return 1
    canonical = JEV_ROUTES[info['route']]['keys'][0]
    rewrite = canonical if info['key_source'] == '.env' and info['key_variable'] == canonical else None
    try:
        save_settings(env_path, updated, rewrite)
    except OSError:
        print('JEV works, but .env could not be updated. Set DECISION_PROVIDER=jev in .env yourself.')
        return 1
    print(f"JEV enabled in .env (DECISION_PROVIDER=jev, JEV_ROUTE={info['route']}). Start or restart HoodScout to use it.")
    return 0
