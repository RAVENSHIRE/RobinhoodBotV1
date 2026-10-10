"""Read-only JEV authentication diagnosis. Never prints or writes a credential.

Uses the existing project virtual environment; does not import/start HoodScout.
Requests follow https://docs.typesafe.ai/introduction/quickstart and the official
typesafe-sdk 0.7.4 model-list endpoint. One inference can consume credits.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys

import httpx
from dotenv import dotenv_values
from dotenv.parser import parse_stream


API = 'https://api.typesafe.ai'
KEY_NAME = 'TYPESAFE_API_KEY'
MODEL = 'jev-latest'
PROBE = {
    'state': {'purpose': 'authentication_diagnostic', 'synthetic': True,
              'trading_allowed': False},
    'model': MODEL,
    'questions': {'check': {'type': 'noul',
                          'instructions': 'Is the state marked synthetic?'}},
}


def inspect_env(project: Path):
    """Read explicit .env without altering os.environ or exposing its contents."""
    path = project / '.env'
    if not path.is_file():
        return {'status': 'missing_project_env', 'key_source': 'project/.env'}, None
    try:
        source = path.read_text(encoding='utf-8')
        bindings = list(parse_stream(io.StringIO(source)))
        literal = dotenv_values(stream=io.StringIO(source), interpolate=False)
        expanded = dotenv_values(stream=io.StringIO(source), interpolate=True)
    except (OSError, UnicodeError):
        return {'status': 'unreadable_project_env', 'key_source': 'project/.env'}, None
    raw_key = expanded.get(KEY_NAME) or ''
    key = raw_key.strip()
    inherited = os.environ.get(KEY_NAME)
    issues = []
    count = sum(binding.key == KEY_NAME for binding in bindings)
    if source.startswith('\ufeff'):
        issues.append('utf8_bom_in_env')
    if any(binding.error for binding in bindings):
        issues.append('dotenv_parse_error')
    if count > 1:
        issues.append('duplicate_key_entries')
    if not key:
        issues.append('missing_key')
    elif not key.isascii() or not key.isprintable() or ' ' in key:
        issues.append('key_contains_non_ascii_whitespace_or_control_characters')
    if key.lower().startswith('bearer '):
        issues.append('key_includes_bearer_prefix')
    if key.startswith(('"', "'")) or key.endswith(('"', "'")):
        issues.append('literal_quote_in_key')
    if literal.get(KEY_NAME) != expanded.get(KEY_NAME):
        issues.append('dotenv_interpolation_changes_key')
    if inherited is not None and inherited.strip() != key:
        issues.append('inherited_key_differs_from_env')
    report = {
        'status': 'local_format_issue' if issues else 'local_format_ok',
        'key_source': 'project/.env', 'key_present': bool(key),
        'key_entries': count, 'issues': issues,
        'surrounding_whitespace_trimmed': raw_key != key,
        'configured_model_is_jev_latest': expanded.get('JEV_MODEL', MODEL) == MODEL,
        'configured_endpoint_is_official': expanded.get('JEV_ENDPOINT', API + '/v1/systemone') == API + '/v1/systemone',
        'proxy_environment_present': any(os.environ.get(name) for name in
                                       ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')),
    }
    return report, key if not issues else None


def safe_response(response, key):
    """Only allowlisted metadata; no raw body, headers or exception strings."""
    request_id = response.headers.get('x-typesafe-request-id', '')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', request_id) or key in request_id:
        request_id = None
    result = {'http_status': response.status_code, 'typesafe_request_id': request_id}
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.is_error:
        # Report words mentioned by the service, not its uncontrolled message.
        # Remove the key before classification so key text cannot affect the result.
        messages = []
        if isinstance(body, dict):
            for field in ('error', 'message', 'detail'):
                value = body.get(field)
                if isinstance(value, dict):
                    value = value.get('message')
                if isinstance(value, str):
                    messages.append(value.replace(key, '').lower())
        words = set(re.findall(r'[a-z]+', ' '.join(messages)))
        result['server_message_mentions'] = sorted(words.intersection(
            {'expired', 'revoked', 'invalid', 'missing', 'unauthorized', 'disabled',
             'inactive', 'permission', 'billing', 'credits', 'signature'}))
    return result, body


def check_remote(key, client_factory=httpx.Client):
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
               'Accept': 'application/json'}
    report = {}
    # Never follow redirects, switch hosts, disable TLS verification, or retry.
    with client_factory(timeout=15, follow_redirects=False) as client:
        for name, method, path, payload in (
                ('model_list', 'GET', '/v1/models', None),
                ('minimal_systemone', 'POST', '/v1/systemone', PROBE)):
            try:
                response = client.request(method, API + path, headers=headers, json=payload)
                result, body = safe_response(response, key)
                if name == 'minimal_systemone':
                    answer = body.get('answers', {}).get('check') if isinstance(body, dict) and isinstance(body.get('answers'), dict) else None
                    value = answer.get('noul') if isinstance(answer, dict) else None
                    result['valid_inference_response'] = (
                        response.status_code == 200 and isinstance(answer, dict)
                        and answer.get('type') == 'noul' and type(value) in (int, float)
                        and 0 <= value <= 1)
                report[name] = result
                if response.status_code == 429:
                    break
            except httpx.TimeoutException:
                report[name] = {'error': 'timeout'}
                break
            except httpx.HTTPError:
                report[name] = {'error': 'network_error'}
                break
    report['systemone_verified'] = report.get('minimal_systemone', {}).get('valid_inference_response', False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
    local, key = inspect_env(args.project.resolve())
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'local': local, 'project_files_changed': False, 'transactions_sent': 0}
    if key:
        report['remote'] = check_remote(key)
    print(json.dumps(report, indent=2))
    if report.get('remote', {}).get('systemone_verified'):
        print('Minimal TypeSafe inference succeeded. Run the HoodScout JEV setup to verify its full request and enable the provider.')
        return 0
    if key:
        print('JEV is not verified. Use the HTTP statuses, UTC time and TypeSafe request ID for diagnosis/support; never send your key.')
    else:
        print('Local configuration issue found. No credential was sent. Correct the listed issue in VS Code and run this check again.')
    return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError):
        print('Diagnostic stopped due to a local configuration or response error. No credentials are included in this message.')
        raise SystemExit(1)
