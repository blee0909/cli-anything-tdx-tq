"""Per-invocation evidence captured before the vendor SDK discards RPC errors.

No account data, order payloads or raw response bodies are retained or logged.
The SDK still owns calls, decoding, paging and connection recovery.
"""
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass

from .errors import (TdxAccountStateUnknownError, TdxDataError,
                     TdxExecutionUnknownError, TdxOperationUnknownError,
                     TdxReadTimeoutError)


TRADE_METHODS = {'order_stock', 'cancel_order_stock'}
ACK_METHODS = {
    'exec_to_tdx', 'send_message', 'send_warn', 'send_user_block',
    'create_sector', 'delete_sector', 'rename_sector', 'clear_sector',
    'refresh_cache', 'refresh_kline',
}
SIDE_EFFECT_ENDPOINTS = {
    'order_stock': ('GetOrderStr', '4'), 'cancel_order_stock': ('GetOrderStr', '5'),
    'exec_to_tdx': ('SetMsgToMain', '15'), 'send_message': ('SetMsgToMain', '2'),
    'send_warn': ('SetMsgToMain', '4'), 'send_user_block': ('SetMsgToMain', '1'),
    'create_sector': ('SetMsgToMain', '11'), 'delete_sector': ('SetMsgToMain', '12'),
    'rename_sector': ('SetMsgToMain', '13'), 'clear_sector': ('SetMsgToMain', '14'),
    'refresh_cache': ('GetTdxDataStr', '16'), 'refresh_kline': ('GetTdxDataStr', '17'),
}
NATIVE_FUNCTIONS = ('GetTdxDataStr', 'GetProDataInStr', 'TdxFuncMain',
                    'GetOrderStr', 'SetMsgToMain')
BATCH_READ_METHODS = {'get_market_data', 'get_financial_data', 'get_financial_data_by_date'}
ACTIVE_TRACE = ContextVar('tdx_rpc_trace', default=None)


def error_id(obj):
    """Only a well-formed numeric ErrorId is a protocol acknowledgement."""
    value = obj.get('ErrorId') if isinstance(obj, dict) else None
    if type(value) is int:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r'0|-?[1-9][0-9]*', value):
        return value
    return None


@dataclass
class NativeCall:
    function: str
    request_type: str | None
    mutation: bool


@dataclass
class NativeFailure:
    call: NativeCall
    kind: str
    error_id: str | None = None


class RpcTrace:
    def __init__(self, method):
        self.method = method
        self.last_call = None
        self.failure = None
        self.dispatched = False
        self.confirmed_reply = False
        self.account_unavailable = False
        self.item_errors = {}
        self.native_nonempty = False

    def begin(self, function, args):
        try:
            request = json.loads(args[1])
            request_type = str(request['type']) if 'type' in request else None
        except (IndexError, TypeError, ValueError):
            request_type = None
        endpoint, request_id = SIDE_EFFECT_ENDPOINTS.get(self.method, (None, None))
        mutation = function == endpoint and request_type in (request_id, None)
        if self.method in TRADE_METHODS and function == endpoint:
            mutation = request_type not in ('0', '1', '2', '3')
        call = NativeCall(function, request_type, mutation)
        self.last_call = call
        self.dispatched |= mutation
        return call

    def fail(self, call, kind, error_id=None):
        # A later success or connection recovery cannot erase an earlier failure.
        if self.failure is None:
            self.failure = NativeFailure(call, kind, error_id)

    def response(self, call, value):
        if value is None or value == b'' or value == '':
            self.fail(call, 'empty_response')
            return
        try:
            obj = json.loads(value)
        except (ValueError, TypeError, UnicodeError):
            self.fail(call, 'invalid_json')
            return
        self.document(obj, call)

    def document(self, obj, call=None):
        call = call or self.last_call
        if call is None:
            return
        # Transport pages are envelopes for chunks of the eventual JSON reply.
        if (isinstance(obj, dict) and obj.get('TPythPaged')
                and ('ErrorId' not in obj or error_id(obj) == '0')):
            return
        code = error_id(obj)
        if code is None:
            self.fail(call, 'invalid_envelope')
            return
        account_call = (self.method == 'stock_account' and call.function == 'GetOrderStr'
                        and call.request_type == '0')
        # Verified for the supported SDK: account lookup ErrorId=9 means
        # "account not logged in or does not exist". It is not a global mapping.
        if account_call and code == '9':
            self.account_unavailable = True
            return
        if code != '0':
            self.fail(call, 'rejected', code)
            return
        self.confirmed_reply = True
        value = obj.get('Value')
        if isinstance(value, (dict, list)) and value:
            self.native_nonempty = True
        if self.method in BATCH_READ_METHODS and isinstance(obj.get('Errors'), dict):
            for symbol, error in obj['Errors'].items():
                if isinstance(error, dict):
                    code = error_id(error)
                    message = error.get('Error') or error.get('Msg') or error.get('message') or 'SDK item error'
                else:
                    code, message = None, str(error)
                self.item_errors.setdefault(str(symbol), {'code': code, 'message': str(message)[:500]})
        if account_call and type(obj.get('Value')) is int and obj['Value'] == -1:
            self.account_unavailable = True

    def read_metadata(self):
        return {'errors': dict(self.item_errors)}

    def check(self):
        failure = self.failure
        if failure is None:
            return
        details = {'method': self.method, 'native_function': failure.call.function,
                   'request_type': failure.call.request_type,
                   'failure_kind': failure.kind, 'sdk_error_id': failure.error_id,
                   'dispatch_started': self.dispatched}
        reason = f"{self.method}: {failure.call.function} {failure.kind}"
        if failure.error_id is not None:
            reason += f" (ErrorId={failure.error_id})"
        if self.method == 'stock_account':
            raise TdxAccountStateUnknownError(reason + '；账户状态无法确认', details=details)
        # Only a negative acknowledgement for the mutation itself is a rejection.
        uncertain = failure.kind != 'rejected' or not failure.call.mutation
        if self.dispatched and uncertain:
            error = TdxExecutionUnknownError if self.method in TRADE_METHODS else TdxOperationUnknownError
            raise error(reason + '；操作状态未知，请查询核对，勿直接重复提交', details=details)
        if failure.kind == 'timeout':
            raise TdxReadTimeoutError(reason, details=details)
        raise TdxDataError(reason, details=details)
