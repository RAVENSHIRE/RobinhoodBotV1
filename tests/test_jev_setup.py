from dataclasses import replace
import json
import os

import httpx
import pytest
from dotenv import dotenv_values

from hoodscout.config import load_config, clean_api_key
from hoodscout.decision_engine import QUESTIONS
from hoodscout.jev_setup import MISSING_KEY, inspect_env_key, save_settings, setup_jev

KEY = 'ts_synthetic_not_a_real_key_123'


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # load_config copies .env values into os.environ; keep that inside each test.
    monkeypatch.setattr(os, 'environ', {k: v for k, v in os.environ.items()
                                        if k not in ('TYPESAFE_API_KEY', 'JEV_API_KEY', 'DECISION_PROVIDER', 'JEV_ROUTE',
                                                     'VERCEL_TYPESAFE_API_KEY', 'AI_GATEWAY_API_KEY', 'VERCEL_JEV_MODEL')})
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'pyproject.toml').write_text('')
    (tmp_path / 'src/hoodscout').mkdir(parents=True)


def valid_answer():
    return {'model': 'jev-latest', 'answers': {
        'action': {'type': 'choice', 'choice': 'hold', 'confidence': .9},
        'risk_score': {'type': 'score', 'score': 9, 'confidence': .9,
                       'legend': {str(i): c for i, c in enumerate(QUESTIONS['risk_score']['criteria'])}}}}


def client(systemone=200, models=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append((request.url.path, request.headers['Authorization']))
        if request.url.path == '/v1/models':
            return httpx.Response(models, json={'data': []})
        return httpx.Response(systemone, json=valid_answer() if systemone == 200 else {'error': 'x'})
    return lambda **options: httpx.Client(transport=httpx.MockTransport(handler), **options)


def no_editor(path):
    raise AssertionError('The editor must not open when .env already has a key')


@pytest.mark.parametrize('raw', [KEY, f'"{KEY}"', f"'{KEY}'", f'Bearer {KEY}', f'  {KEY}  '])
def test_copy_paste_artifacts_are_removed(raw):
    assert clean_api_key(raw) == KEY
    assert clean_api_key('your_secret_key') is None and clean_api_key('  ') is None


def test_key_is_read_from_env_and_beats_an_old_inherited_variable(tmp_path):
    os.environ['TYPESAFE_API_KEY'] = 'old-key-from-windows-environment'
    (tmp_path / '.env').write_text(f'DATA_DIR=./data/paper\nTYPESAFE_API_KEY={KEY}\n')
    assert load_config().typesafe_api_key == KEY


@pytest.mark.parametrize('encoding', ['utf-16', 'utf-8-sig'])
def test_powershell_and_bom_encoded_env_files_are_read(tmp_path, encoding):
    (tmp_path / '.env').write_text(f'JEV_API_KEY={KEY}\r\nDATA_DIR=./data/paper\r\n', encoding=encoding)
    assert load_config().typesafe_api_key == KEY
    report, info = inspect_env_key(tmp_path / '.env')
    assert info['key'] == KEY and report['key_variable'] == 'JEV_API_KEY' and report['key_length'] == len(KEY)
    assert KEY not in json.dumps(report)


def test_setup_uses_the_env_key_without_prompting_and_enables_jev(tmp_path, capsys):
    path = tmp_path / '.env'
    path.write_text(f'# User settings\nDATA_DIR=./data/paper\nDECISION_PROVIDER=stub\nTYPESAFE_API_KEY="Bearer {KEY}"\n',
                    encoding='utf-16')
    seen = []
    assert setup_jev(load_config(), path, editor=no_editor, client_factory=client(seen=seen)) == 0
    assert seen == [('/v1/systemone', f'Bearer {KEY}')]
    values = dotenv_values(path)  # rewritten as plain UTF-8
    assert values['TYPESAFE_API_KEY'] == KEY and values['DECISION_PROVIDER'] == 'jev'
    assert values['DATA_DIR'] == './data/paper' and '# User settings' in path.read_text(encoding='utf-8')
    assert not list(tmp_path.glob('.hoodscout-jev-*.tmp'))
    assert KEY not in capsys.readouterr().out


def test_missing_key_adds_a_placeholder_and_opens_the_editor(tmp_path):
    path = tmp_path / '.env'
    path.write_text('DATA_DIR=./data/paper')  # no trailing newline

    def paste_key(target):
        target.write_text(target.read_text().replace('VERCEL_TYPESAFE_API_KEY=', f'VERCEL_TYPESAFE_API_KEY={KEY}'))
        return True
    assert setup_jev(load_config(), path, editor=paste_key, client_factory=client()) == 0
    values = dotenv_values(path)
    assert values['VERCEL_TYPESAFE_API_KEY'] == KEY and values['JEV_ROUTE'] == 'vercel'

    path.write_text('DATA_DIR=./data/paper\n')
    assert setup_jev(load_config(), path, editor=lambda target: False, client_factory=client()) == MISSING_KEY
    assert path.read_text() == 'DATA_DIR=./data/paper\nVERCEL_TYPESAFE_API_KEY=\n'


@pytest.mark.parametrize('models,hint', [(401, 'Create a new API key'), (200, 'System One / JEV API access')])
def test_rejected_key_is_explained_and_env_is_unchanged(tmp_path, capsys, models, hint):
    path = tmp_path / '.env'
    original = f'TYPESAFE_API_KEY={KEY}\nDECISION_PROVIDER=stub\n'
    path.write_text(original)
    seen = []
    assert setup_jev(load_config(), path, editor=no_editor, client_factory=client(401, models, seen)) == 1
    assert [p for p, _ in seen] == ['/v1/systemone', '/v1/models']
    assert path.read_text() == original
    out = capsys.readouterr().out
    assert 'JEV NOT CONNECTED' in out and hint in out and KEY not in out


def test_save_settings_preserves_other_entries_and_quotes(tmp_path):
    path = tmp_path / '.env'
    path.write_text('# User settings\nETH_USD=1234\nDATA_DIR=./data/paper\nDECISION_PROVIDER=stub\n')
    save_settings(path, replace(load_config(path), typesafe_api_key="synthetic'key", jev_route='typesafe'),
                  'TYPESAFE_API_KEY')
    values = dotenv_values(path)
    assert values['ETH_USD'] == '1234' and values['DATA_DIR'] == './data/paper'
    assert values['TYPESAFE_API_KEY'] == "synthetic'key" and values['DECISION_PROVIDER'] == 'jev'
    assert values['JEV_ROUTE'] == 'typesafe' and '# User settings' in path.read_text()


def test_several_key_lines_use_the_last_filled_in_one(tmp_path):
    path = tmp_path / '.env'
    path.write_text(f'TYPESAFE_API_KEY=old_key_one\nTYPESAFE_API_KEY={KEY}\nTYPESAFE_API_KEY=\n')
    report, info = inspect_env_key(path)
    assert info['key'] == KEY and report['notes'] == ['several_key_lines_last_filled_one_used']
    assert load_config().typesafe_api_key == KEY


VERCEL = 'https://ai-gateway.vercel.sh/typesafe/v1/systemone'


def test_auto_route_prefers_a_vercel_key_and_pins_endpoint_and_model(tmp_path):
    (tmp_path / '.env').write_text(f'TYPESAFE_API_KEY=typesafe_key_without_credit\nVERCEL_TYPESAFE_API_KEY={KEY}\n'
                                   'JEV_ENDPOINT=https://api.typesafe.ai/v1/systemone\nJEV_MODEL=jev-latest\n')
    cfg = load_config()
    assert (cfg.jev_route, cfg.typesafe_api_key, cfg.jev_endpoint, cfg.jev_model) == \
        ('vercel', KEY, VERCEL, 'typesafe-ai/jev')
    os.environ['JEV_ROUTE'] = 'typesafe'
    cfg = load_config()
    assert (cfg.jev_route, cfg.typesafe_api_key, cfg.jev_endpoint) == \
        ('typesafe', 'typesafe_key_without_credit', 'https://api.typesafe.ai/v1/systemone')


def test_gateway_key_from_the_windows_environment_is_used_when_env_has_none(tmp_path):
    (tmp_path / '.env').write_text('TYPESAFE_API_KEY=typesafe_key\n')
    os.environ['AI_GATEWAY_API_KEY'] = KEY
    cfg = load_config()
    assert cfg.jev_route == 'vercel' and cfg.typesafe_api_key == KEY
    os.environ['JEV_ROUTE'] = 'nonsense'
    with pytest.raises(ValueError, match='JEV_ROUTE'):
        load_config()


def test_setup_through_vercel_sends_the_gateway_request_and_pins_the_route(tmp_path, capsys):
    path = tmp_path / '.env'
    path.write_text(f'TYPESAFE_API_KEY=typesafe_key\nVERCEL_TYPESAFE_API_KEY=" {KEY} "\nDECISION_PROVIDER=stub\n')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=valid_answer())

    def factory(**options):
        return httpx.Client(transport=httpx.MockTransport(handler), **options)
    assert setup_jev(load_config(), path, editor=no_editor, client_factory=factory) == 0
    sent = requests[0]
    assert str(sent.url) == VERCEL and sent.headers['Authorization'] == f'Bearer {KEY}'
    assert json.loads(sent.content)['model'] == 'typesafe-ai/jev'
    values = dotenv_values(path)
    assert values['VERCEL_TYPESAFE_API_KEY'] == KEY and values['JEV_ROUTE'] == 'vercel'
    assert values['TYPESAFE_API_KEY'] == 'typesafe_key' and values['DECISION_PROVIDER'] == 'jev'
    assert KEY not in capsys.readouterr().out


@pytest.mark.parametrize('status,hint', [(401, 'AI Gateway > API Keys'), (403, 'payment method'),
                                         (402, 'AI Gateway billing')])
def test_vercel_errors_are_explained_without_a_typesafe_probe(tmp_path, capsys, status, hint):
    path = tmp_path / '.env'
    path.write_text(f'VERCEL_TYPESAFE_API_KEY={KEY}\n')
    seen = []
    assert setup_jev(load_config(), path, editor=no_editor, client_factory=client(status, 200, seen)) == 1
    assert len(seen) == 1 and path.read_text() == f'VERCEL_TYPESAFE_API_KEY={KEY}\n'
    out = capsys.readouterr().out
    assert hint in out and 'Vercel AI Gateway' in out and KEY not in out
