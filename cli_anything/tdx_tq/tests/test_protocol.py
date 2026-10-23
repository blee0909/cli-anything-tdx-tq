"""RPC contract tests: all native functions are in-memory fakes, including writes."""
import json
import types

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.errors import (
    TdxAccountStateUnknownError, TdxDataError, TdxExecutionUnknownError,
    TdxInitError, TdxOperationUnknownError, TdxParamError, TdxReadTimeoutError,
)
from cli_anything.tdx_tq.core.protocol import ACTIVE_TRACE, SIDE_EFFECT_ENDPOINTS
from cli_anything.tdx_tq.core.sdk import TqAdapter, patch_module, validate_result
from cli_anything.tdx_tq.core.trading import OrderRequest, cancel_order, submit_order
from cli_anything.tdx_tq.tests.test_contracts import Session
from cli_anything.tdx_tq.utils.cli import error_payload


READ_ENDPOINTS = {
    'stock_account': ('GetOrderStr', '0'),
    'query_stock_asset': ('GetOrderStr', '1'),
    'query_stock_orders': ('GetOrderStr', '2'),
    'query_stock_positions': ('GetOrderStr', '3'),
    'get_financial_data': ('GetProDataInStr', '1'),
    'get_financial_data_by_date': ('GetProDataInStr', '2'),
}


def reply(code='0', value=None):
    return json.dumps({'ErrorId': code, 'Value': value}).encode('utf-8')


def make_sdk(method, response):
    """Model the SDK's observed error-to-sentinel conversion, not a real DLL."""
    function, request_type = (SIDE_EFFECT_ENDPOINTS | READ_ENDPOINTS)[method]
    state = types.SimpleNamespace(calls=[], response=response)
    def native(*args):
        state.calls.append(json.loads(args[1]))
        if isinstance(state.response, BaseException):
            raise state.response
        return state.response
    module = types.SimpleNamespace(dll=types.SimpleNamespace(**{function: native}),
                                   tq=types.SimpleNamespace(), __file__=__file__)
    def parse(payload):
        obj = json.loads(payload)
        if str(obj.get('ErrorId')) == '20':
            raise SystemExit('vendor guard')
        return obj
    module._json_loads_with_errorid_guard = parse
    fallback = -1 if method in ('stock_account', 'order_stock', 'cancel_order_stock') else (
        [] if method in ('query_stock_orders', 'query_stock_positions') else {})
    def operation(**kwargs):
        try:
            raw = getattr(module.dll, function)(1, json.dumps({'type': request_type}).encode(), 100)
            obj = module._json_loads_with_errorid_guard(raw)
            if obj.get('ErrorId') != '0':
                return fallback
            if method in SIDE_EFFECT_ENDPOINTS or method == 'query_stock_asset':
                return obj
            return obj['Value']
        except Exception:
            return fallback
    setattr(module.tq, method, operation)
    state.module = module
    state.sdk = TqAdapter(module)
    return state


@pytest.mark.parametrize('method', READ_ENDPOINTS)
@pytest.mark.parametrize('response', [b'', None, b'not-json', b'[]', b'{}', reply('6'), ConnectionError('lost')])
def test_transport_failure_is_not_successful_empty_data(method, response):
    state = make_sdk(method, response)
    error = TdxAccountStateUnknownError if method == 'stock_account' else TdxDataError
    with pytest.raises(error) as caught:
        getattr(state.sdk, method)()
    assert len(state.calls) == 1
    assert caught.value.details['native_function'] == READ_ENDPOINTS[method][0]
    assert caught.value.details['dispatch_started'] is False
    assert ACTIVE_TRACE.get() is None


@pytest.mark.parametrize('method,value', [
    ('query_stock_orders', []), ('query_stock_positions', []),
    ('get_financial_data', {}), ('get_financial_data_by_date', {}),
    ('stock_account', 0), ('stock_account', -1),
])
def test_confirmed_empty_results_remain_valid(method, value):
    state = make_sdk(method, reply(value=value))
    assert getattr(state.sdk, method)() == value


def test_account_unavailability_is_scoped_to_account_lookup():
    state = make_sdk('stock_account', reply('9'))
    assert state.sdk.stock_account() == -1
    state = make_sdk('query_stock_positions', reply('9'))
    with pytest.raises(TdxDataError):
        state.sdk.query_stock_positions()


@pytest.mark.parametrize('value', [True, None, {}, '1', -2])
def test_account_handle_has_a_strict_type(value):
    with pytest.raises(TdxAccountStateUnknownError):
        validate_result(value, 'stock_account')


@pytest.mark.parametrize('method', ['order_stock', 'cancel_order_stock'])
@pytest.mark.parametrize('response', [b'', b'invalid', b'{}', ConnectionError('lost'),
                                      TimeoutError('deadline'), KeyboardInterrupt(), SystemExit('stop'),
                                      reply(None), reply(False), reply([]), reply('bad')])
def test_unconfirmed_mutation_is_unknown_and_never_retried(method, response):
    state = make_sdk(method, response)
    with pytest.raises(TdxExecutionUnknownError) as caught:
        getattr(state.sdk, method)()
    assert len(state.calls) == 1
    assert caught.value.retryable is False
    assert caught.value.details['dispatch_started'] is True


@pytest.mark.parametrize('method', SIDE_EFFECT_ENDPOINTS)
def test_explicit_negative_acknowledgement_is_a_failure(method):
    state = make_sdk(method, reply('5'))
    with pytest.raises(TdxDataError) as caught:
        getattr(state.sdk, method)()
    assert caught.value.details['failure_kind'] == 'rejected'
    assert caught.value.details['sdk_error_id'] == '5'
    assert len(state.calls) == 1


@pytest.mark.parametrize('method', SIDE_EFFECT_ENDPOINTS)
def test_confirmed_mutations_accept_the_original_receipt(method):
    state = make_sdk(method, reply(value='fake-receipt'))
    assert getattr(state.sdk, method)()['ErrorId'] == '0'
    assert len(state.calls) == 1


@pytest.mark.parametrize('method', sorted(set(SIDE_EFFECT_ENDPOINTS) - {'order_stock', 'cancel_order_stock'}))
def test_client_and_sector_operations_need_confirmation(method):
    state = make_sdk(method, b'')
    with pytest.raises(TdxOperationUnknownError):
        getattr(state.sdk, method)()
    with pytest.raises(TdxOperationUnknownError):
        validate_result({}, method)


@pytest.mark.parametrize('method', ['create_sector', 'refresh_cache'])
def test_vendor_json_string_receipts_are_checked_without_changing_output(method):
    good = reply().decode()
    assert validate_result(good, method) == good
    with pytest.raises(TdxDataError):
        validate_result(reply('6').decode(), method)
    with pytest.raises(TdxOperationUnknownError):
        validate_result('not-json', method)


def test_read_timeout_keeps_retry_metadata():
    state = make_sdk('query_stock_orders', TimeoutError())
    with pytest.raises(TdxReadTimeoutError) as caught:
        state.sdk.query_stock_orders()
    assert caught.value.retryable is True


@pytest.mark.parametrize('error', [ConnectionError('lost'), RuntimeError('after dispatch'),
                                   KeyboardInterrupt(), SystemExit('SDK exit')])
def test_domain_services_protect_non_adapter_backends(error):
    calls = []
    def lose(**kwargs):
        calls.append(kwargs)
        raise error
    sdk = types.SimpleNamespace(order_stock=lose, cancel_order_stock=lose)
    with pytest.raises(TdxExecutionUnknownError):
        submit_order(sdk, OrderRequest('600519.SH', 'buy', 100, 10.), 1)
    with pytest.raises(TdxExecutionUnknownError):
        cancel_order(sdk, 1, 'fake-order', '600519.sh')
    assert len(calls) == 2
    assert calls[-1]['stock_code'] == '600519.SH'


def test_before_dispatch_errors_are_not_unknown_or_submitted():
    state = make_sdk('order_stock', reply())
    def reject(**kwargs):
        raise ValueError('before native call')
    state.module.tq.order_stock = reject
    with pytest.raises(TdxParamError):
        submit_order(state.sdk, OrderRequest('600519.SH', 'buy', 100, 10.), 1)
    assert not state.calls
    state.module.tq.order_stock = lambda required: None
    with pytest.raises(TdxParamError):
        state.sdk.order_stock()
    del state.module.dll.GetOrderStr
    with pytest.raises(TdxInitError):
        state.sdk.order_stock()


def test_unverified_success_and_failed_sdk_validation_are_not_accepted():
    state = make_sdk('order_stock', reply())
    state.module.tq.order_stock = lambda: {'ErrorId': '0'}
    with pytest.raises(TdxDataError):
        state.sdk.order_stock()
    state = make_sdk('query_stock_orders', reply())
    state.module.tq.query_stock_orders = lambda: []
    with pytest.raises(TdxDataError):
        state.sdk.query_stock_orders()


def test_later_success_cannot_erase_an_earlier_native_failure():
    state = make_sdk('query_stock_orders', b'')
    def operation():
        state.module.dll.GetOrderStr(1, b'{"type":2}', 100)
        state.response = reply(value=[])
        state.module.dll.GetOrderStr(1, b'{"type":2}', 100)
        return []
    state.module.tq.query_stock_orders = operation
    with pytest.raises(TdxDataError):
        state.sdk.query_stock_orders()


@pytest.mark.parametrize('code,error', [('0', None), ('6', TdxDataError)])
def test_reassembled_transport_pages_are_also_checked(code, error):
    page = json.dumps({'TPythPaged': True, 'page_data': reply(code, []).decode()}).encode()
    state = make_sdk('query_stock_orders', page)
    def operation():
        outer = state.module._json_loads_with_errorid_guard(state.module.dll.GetOrderStr(1, b'{"type":2}', 100))
        inner = state.module._json_loads_with_errorid_guard(outer['page_data'])
        return inner['Value']
    state.module.tq.query_stock_orders = operation
    if error:
        with pytest.raises(error): state.sdk.query_stock_orders()
    else:
        assert state.sdk.query_stock_orders() == []


def test_error_metadata_excludes_private_request_and_response_fields():
    state = make_sdk('order_stock', b'{"ErrorId":"6","Error":"private-account-token"}')
    with pytest.raises(TdxDataError) as caught:
        state.sdk.order_stock()
    payload = error_payload(caught.value)
    assert 'private-account-token' not in json.dumps(payload)
    assert payload['details']['sdk_error_id'] == '6'


def test_cli_account_distinguishes_unavailable_from_unconfirmed(monkeypatch):
    state = make_sdk('stock_account', reply('9'))
    session = types.SimpleNamespace(tdx=state.sdk, ensure_initialized=lambda: None, close=lambda: None)
    monkeypatch.setattr(cli, 'session', session)
    result = CliRunner().invoke(cli.cli, ['trade', 'account', '--json'])
    assert result.exit_code == 0
    assert json.loads(result.stdout)['state'] == 'unavailable'
    state.response = b''
    result = CliRunner().invoke(cli.cli, ['trade', 'account', '--json'])
    assert result.exit_code == 1
    assert json.loads(result.stdout)['error_code'] == 'ACCOUNT_STATE_UNKNOWN'


def test_dividend_json_keeps_the_actual_date_index(monkeypatch):
    fake = Session()
    frame = pd.DataFrame({'Bonus': [1.2]}, index=pd.DatetimeIndex(['2026-10-20'], name='Date'))
    fake.raw.get_divid_factors = lambda **kwargs: frame
    monkeypatch.setattr(cli, 'session', fake)
    result = CliRunner().invoke(cli.cli, ['market', 'divid', '-s', '600519.SH', '--json'])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{'Date': '2026-10-20T00:00:00', 'Bonus': 1.2}]
    result = CliRunner().invoke(cli.cli, ['market', 'divid', '-s', '600519.SH', '--start', '20260230', '--json'])
    assert result.exit_code == 2
    assert json.loads(result.stdout)['error_code'] == 'INVALID_PARAMETER'


def test_transport_page_error_is_not_hidden_by_its_successful_inner_body():
    page = json.dumps({'TPythPaged': True, 'ErrorId': '6', 'page_data': reply('0', []).decode()}).encode()
    state = make_sdk('query_stock_orders', page)
    with pytest.raises(TdxDataError):
        state.sdk.query_stock_orders()


@pytest.mark.parametrize('result', [-1, False, {'ErrorId': None}, {'ErrorId': False},
                                    {'error': 'response lost'}, {'ErrorId': '0', 'error': 'lost'}])
def test_non_acknowledgements_are_not_definite_trade_rejections(result):
    with pytest.raises(TdxExecutionUnknownError):
        validate_result(result, 'order_stock', trade=True)
