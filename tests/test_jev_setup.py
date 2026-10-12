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
                                        if k not in ('TYPESAFE_API_KEY', 'JEV_API_KEY', 'DECISION_PROVIDER')})
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
    report, key = inspect_env_key(tmp_path / '.env')
    assert key == KEY and report['key_variable'] == 'JEV_API_KEY' and report['key_length'] == len(KEY)
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
        target.write_text(target.read_text().replace('TYPESAFE_API_KEY=', f'TYPESAFE_API_KEY={KEY}'))
        return True
    assert setup_jev(load_config(), path, editor=paste_key, client_factory=client()) == 0
    assert dotenv_values(path)['TYPESAFE_API_KEY'] == KEY

    path.write_text('DATA_DIR=./data/paper\n')
    assert setup_jev(load_config(), path, editor=lambda target: False, client_factory=client()) == MISSING_KEY
    assert path.read_text() == 'DATA_DIR=./data/paper\nTYPESAFE_API_KEY=\n'


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
    save_settings(path, replace(load_config(path), typesafe_api_key="synthetic'key"))
    values = dotenv_values(path)
    assert values['ETH_USD'] == '1234' and values['DATA_DIR'] == './data/paper'
    assert values['TYPESAFE_API_KEY'] == "synthetic'key" and values['DECISION_PROVIDER'] == 'jev'
    assert '# User settings' in path.read_text()


def test_several_key_lines_use_the_last_filled_in_one(tmp_path):
    path = tmp_path / '.env'
    path.write_text(f'TYPESAFE_API_KEY=old_key_one\nTYPESAFE_API_KEY={KEY}\nTYPESAFE_API_KEY=\n')
    report, key = inspect_env_key(path)
    assert key == KEY and report['notes'] == ['several_key_lines_last_filled_one_used']
    assert load_config().typesafe_api_key == KEY
