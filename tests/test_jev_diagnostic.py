import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_script = Path(__file__).resolve().parents[1] / 'downloads/diagnose_jev.py'
spec = importlib.util.spec_from_file_location('jev_diagnostic', _script)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def test_env_key_is_used_without_changing_process_or_files(tmp_path, monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    path = tmp_path / '.env'
    source = 'TYPESAFE_API_KEY="synthetic-test-key"\nDECISION_PROVIDER=stub\n'
    path.write_text(source)
    report, key = diagnostic.inspect_env(tmp_path)
    assert report['status'] == 'local_format_ok' and key == 'synthetic-test-key'
    assert path.read_text() == source
    assert 'synthetic-test-key' not in json.dumps(report)


@pytest.mark.parametrize('source,issue', [
    ('TYPESAFE_API_KEY=Bearer synthetic\n', 'key_includes_bearer_prefix'),
    ('TYPESAFE_API_KEY=one\nTYPESAFE_API_KEY=two\n', 'duplicate_key_entries'),
    ('TYPESAFE_API_KEY="synthetic\x16key"\n', 'key_contains_non_ascii_whitespace_or_control_characters'),
    ('TYPESAFE_API_KEY=synthetic${LOCAL_MARKER}\n', 'dotenv_interpolation_changes_key'),
    ('\ufeffTYPESAFE_API_KEY=synthetic\n', 'utf8_bom_in_env'),
])
def test_local_format_errors_block_requests(tmp_path, monkeypatch, source, issue):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.setenv('LOCAL_MARKER', 'changed')
    (tmp_path / '.env').write_text(source, encoding='utf-8')
    report, key = diagnostic.inspect_env(tmp_path)
    assert issue in report['issues'] and key is None


def test_different_inherited_key_is_reported_not_silently_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'inherited-synthetic')
    (tmp_path / '.env').write_text('TYPESAFE_API_KEY=local-synthetic\n')
    report, key = diagnostic.inspect_env(tmp_path)
    assert key is None and 'inherited_key_differs_from_env' in report['issues']
    assert 'inherited-synthetic' not in json.dumps(report)


def test_authentication_failures_preserve_safe_request_id_without_response_secrets():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer synthetic-test-key'
        return httpx.Response(401, headers={'x-typesafe-request-id': 'request-123'},
                              json={'error': {'message': 'Invalid key synthetic-test-key'}})
    report = diagnostic.check_remote('synthetic-test-key', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    assert len(calls) == 2 and not report['systemone_verified']
    assert report['minimal_systemone']['typesafe_request_id'] == 'request-123'
    assert report['minimal_systemone']['server_message_mentions'] == ['invalid']
    assert 'synthetic-test-key' not in json.dumps(report)


def test_model_list_success_does_not_prove_inference_access():
    def handler(request):
        return httpx.Response(200, json={'models': []}) if request.method == 'GET' else httpx.Response(401)
    report = diagnostic.check_remote('synthetic', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    assert not report['systemone_verified']


def test_minimal_inference_success_and_no_transactions():
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'models': []})
        assert json.loads(request.content)['state']['trading_allowed'] is False
        return httpx.Response(200, json={'answers': {'check': {'type': 'noul', 'noul': 1.0}}})
    report = diagnostic.check_remote('synthetic', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    assert report['systemone_verified']


def test_redirect_is_never_followed_and_secret_request_id_is_omitted():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={'Location': 'https://example.com/', 'x-typesafe-request-id': 'synthetic'})
    report = diagnostic.check_remote('synthetic', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    assert len(calls) == 2 and all(request.url.host == 'api.typesafe.ai' for request in calls)
    assert report['minimal_systemone']['typesafe_request_id'] is None


def test_rate_limit_does_not_trigger_second_request():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429)
    report = diagnostic.check_remote('synthetic', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    assert len(calls) == 1 and not report['systemone_verified']
