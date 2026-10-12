import copy
import json

import httpx
import pytest

from hoodscout.decision_engine import DecisionEngine, QUESTIONS, normalize_decision


def valid_response():
    # Synthetic fixture following official typesafe-sdk 0.7.4 OpenAPI models.
    return {'model': 'jev-latest', 'usage': {'input_tokens': 20, 'output_tokens': 5},
            'answers': {
                'action': {'type': 'choice', 'choice': 'hold', 'confidence': .9,
                           'probabilities': {'buy': .05, 'hold': .9, 'skip': .05}},
                'risk_score': {'type': 'score', 'score': 2, 'confidence': .85,
                               'legend': {str(i): description for i, description in enumerate(QUESTIONS['risk_score']['criteria'])},
                               'probabilities': {str(i): float(i == 2) for i in range(10)}},
            }}


def make_engine(handler, **kwargs):
    return DecisionEngine('jev', 'not-a-real-key', client_factory=lambda **options:
        httpx.Client(transport=httpx.MockTransport(handler), **options), **kwargs)


def test_actual_request_shape_risk_scale_and_no_claim_before_response():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer not-a-real-key'
        body = json.loads(request.content)
        assert body['model'] == 'jev-latest'
        assert body['state']['synthetic'] is True
        assert set(body) == {'state', 'questions', 'model'}
        assert len(body['questions']['risk_score']['criteria']) == 10
        assert 'levels' not in body['questions']['risk_score']
        return httpx.Response(200, json=valid_response())

    engine = make_engine(handler)
    assert engine.status()['configured'] and not engine.status()['connected']
    result = engine.check_connection()
    assert result['model_status']['connected']
    assert result['check_only'] and result['transactions_sent'] == 0
    assert result['decision'] == {'action': 'hold', 'confidence': .85, 'risk_score': 3.0}
    assert len(requests) == 1


@pytest.mark.parametrize('status,error', [(401,'authentication_failed'), (402,'payment_required'),
    (403,'access_denied'), (404,'request_rejected'), (422,'request_rejected'),
    (429,'rate_limited'), (503,'http_error')])
def test_http_errors_fail_closed_without_leaking_body_or_key(status, error):
    engine = make_engine(lambda request: httpx.Response(status, text='not-a-real-key'))
    decision, raw = engine.decide({})
    assert decision['action'] == 'hold' and decision['error'] == error
    assert engine.status()['status'] == error and not engine.status()['connected']
    assert engine.status()['http_status'] == status
    assert 'not-a-real-key' not in json.dumps([decision, raw, engine.status()])


@pytest.mark.parametrize('exception,error', [(httpx.ReadTimeout,'timeout'), (httpx.ConnectError,'network_error')])
def test_transport_errors_are_distinguished(exception, error):
    def handler(request):
        raise exception('not-a-real-key', request=request)
    engine = make_engine(handler)
    decision, raw = engine.decide({})
    assert decision['action'] == 'hold' and engine.status()['status'] == error
    assert 'not-a-real-key' not in json.dumps([decision, raw, engine.status()])


def test_bad_json_and_bad_schema_are_not_connected():
    for response in [httpx.Response(200,text='not-json'), httpx.Response(200,json={'hello':'world'})]:
        engine = make_engine(lambda request: response)
        assert engine.decide({})[0]['error'] == 'invalid_model_response'
        assert not engine.status()['connected']


def test_key_presence_is_not_connectivity_and_missing_key_does_not_call_api():
    def forbidden(**kwargs):
        raise AssertionError('No request expected')
    engine = DecisionEngine('jev', None, client_factory=forbidden)
    assert engine.check_connection()['model_status']['status'] == 'missing_key'
    assert not engine.status()['connected']


def test_redirect_is_not_followed_and_custom_credential_destination_is_rejected():
    calls=[]
    def redirect(request):
        calls.append(request)
        return httpx.Response(307,headers={'location':'https://example.com/collect'})
    engine=make_engine(redirect)
    assert engine.decide({})[0]['action']=='hold'
    assert len(calls)==1
    engine.endpoint='https://example.com/v1/systemone'
    assert engine.decide({})[0]['error']=='invalid_endpoint'
    assert len(calls)==1


def test_success_expires_and_subsequent_failure_clears_connected_status():
    clock=[1000.0]
    fail=[False]
    engine=make_engine(lambda request: httpx.Response(401) if fail[0] else httpx.Response(200,json=valid_response()),
                       clock=lambda:clock[0])
    assert engine.check_connection()['model_status']['connected']
    clock[0]+=301
    assert engine.status()['status']=='not_recently_checked'
    fail[0]=True
    engine.check_connection()
    assert not engine.status()['connected'] and engine.status()['last_success_at']==1000


@pytest.mark.parametrize('field,value', [('score',float('nan')),('score',10),('score',-1),
                                       ('confidence',float('inf')),('confidence',True)])
def test_invalid_typed_scores_fail_closed(field,value):
    raw=valid_response();raw['answers']['risk_score'][field]=value
    assert normalize_decision(raw)['action']=='hold'
    assert normalize_decision(raw)['error']=='invalid_model_response'


def test_zero_based_risk_extremes_and_low_risk_confidence():
    for score,expected in [(0,1),(9,10)]:
        raw=valid_response();raw['answers']['risk_score']['score']=score
        assert normalize_decision(raw)['risk_score']==expected
    raw=valid_response();raw['answers']['risk_score']['confidence']=.2
    assert normalize_decision(raw)['confidence']==.2


def test_raw_audit_payload_redacts_secrets():
    payload=valid_response()
    payload.update(authorization='Bearer not-a-real-key',note='echo not-a-real-key')
    engine=make_engine(lambda request:httpx.Response(200,json=payload))
    _,raw=engine.decide({})
    assert 'not-a-real-key' not in json.dumps(raw)
    assert raw['authorization']=='[REDACTED]'


def test_a_key_is_never_sent_to_the_other_routes_endpoint():
    calls = []
    engine = make_engine(lambda request: calls.append(request) or httpx.Response(200, json=valid_response()))
    engine.endpoint = 'https://ai-gateway.vercel.sh/typesafe/v1/systemone'
    assert engine.decide({})[0]['error'] == 'invalid_endpoint' and not calls
    engine.route = 'vercel'
    assert engine.decide({})[0]['action'] == 'hold' and len(calls) == 1
    assert engine.status()['service'] == 'Vercel AI Gateway' and engine.status()['connected']
