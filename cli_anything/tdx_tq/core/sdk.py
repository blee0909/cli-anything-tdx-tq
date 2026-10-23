"""Lazy SDK loading, narrowly scoped compatibility patches and result contracts."""
import builtins
import functools
import importlib.util
import inspect
import json
import logging
import numbers
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

from .errors import (TdxAccountStateUnknownError, TdxDataError, TdxError,
                     TdxExecutionUnknownError, TdxInitError,
                     TdxOperationUnknownError, TdxParamError, TdxReadTimeoutError)
from .protocol import (ACK_METHODS, ACTIVE_TRACE, NATIVE_FUNCTIONS,
                       SIDE_EFFECT_ENDPOINTS, TRADE_METHODS, BATCH_READ_METHODS, RpcTrace, error_id)

logger = logging.getLogger('cli_anything.tdx_tq')
REQUIRE_DATA = {'get_market_snapshot', 'get_market_data', 'get_stock_info'}
READ_TYPES = {
    'query_stock_asset': dict, 'query_stock_positions': list, 'query_stock_orders': list,
    'get_financial_data': dict, 'get_financial_data_by_date': dict,
    'get_gpjy_value': dict, 'get_bkjy_value': dict, 'get_scjy_value': dict,
}


def validate_result(result, method: str, *, require_data: bool = False, trade: bool = False,
                    account_unavailable: bool = False):
    if method == 'stock_account':
        if isinstance(result, numbers.Integral) and not isinstance(result, bool):
            if result >= 0 or (result == -1 and account_unavailable):
                return result
        raise TdxAccountStateUnknownError('无法确认交易账户状态；SDK 失败值不能证明未登录')
    envelope = result
    if method in ACK_METHODS and isinstance(result, str):
        try:
            envelope = json.loads(result)
        except ValueError:
            envelope = None
    code = error_id(envelope)
    if isinstance(envelope, dict):
        if code is not None and code != '0':
            raise TdxDataError(f"{method} 返回错误 (ErrorId={code})")
        if 'error' in envelope and envelope['error'] not in (None, 0, '0', False, ''):
            if not trade and method not in ACK_METHODS:
                raise TdxDataError(f"{method} 返回错误")
            code = None
        if 'ErrorId' in envelope and code is None and not trade and method not in ACK_METHODS:
            raise TdxDataError(f"{method} 返回无效的错误回执")
    confirmed = code == '0'
    if trade and not confirmed:
        raise TdxExecutionUnknownError(f"{method} 未返回可确认的成功响应；请查询委托状态后核对")
    if method in ACK_METHODS and not confirmed:
        raise TdxOperationUnknownError(f"{method} 未返回可确认的操作回执；请核对后再操作")
    if result is False or (isinstance(result, numbers.Real) and result == -1):
        raise TdxDataError(f"{method} 返回失败值，请核对客户端状态与错误日志")
    if method in READ_TYPES and not isinstance(result, READ_TYPES[method]):
        raise TdxDataError(f"{method} 返回的数据类型不符合接口契约")
    require_data |= method == 'query_stock_asset'
    if require_data and (result is None or (hasattr(result, '__len__') and len(result) == 0)):
        raise TdxDataError(f"{method} 没有返回可用数据，请核对证券、缓存、日期和权限")
    return result


def patch_module(mod) -> None:
    """Redirect SDK prints and repair only the known GBK/empty-bond cases."""
    def quiet_print(*args, **kwargs):
        if args and isinstance(args[0], str) and any(k in args[0] for k in ('TQ数据', '初始化成功', '已关闭')):
            return
        kwargs['file'] = sys.stderr
        builtins.print(*args, **kwargs)
    mod.print = quiet_print
    dll = getattr(mod, 'dll', None)
    if dll is not None:
        functions = []
        for name in NATIVE_FUNCTIONS:
            fn = getattr(dll, name, None)
            if fn is None:
                continue
            functions.append(name)
            if getattr(fn, '_rpc_evidence_patched', False):
                continue
            def wrap(original, function):
                @functools.wraps(original)
                def wrapped(*args, **kwargs):
                    trace = ACTIVE_TRACE.get()
                    evidence = trace.begin(function, args) if trace is not None else None
                    try:
                        value = original(*args, **kwargs)
                    except (Exception, SystemExit, KeyboardInterrupt) as exc:
                        if trace is not None:
                            trace.fail(evidence, 'timeout' if isinstance(exc, TimeoutError) else 'exception')
                        raise
                    if isinstance(value, bytes):
                        try:
                            value.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                value = value.decode('gbk').encode('utf-8')
                            except UnicodeDecodeError:
                                logger.debug('SDK response is neither UTF-8 nor GBK')
                    if trace is not None:
                        trace.response(evidence, value)
                    return value
                wrapped._rpc_evidence_patched = True
                return wrapped
            setattr(dll, name, wrap(fn, name))
        dll._rpc_evidence_patched = True
        dll._rpc_evidence_functions = functions
    parser = getattr(mod, '_json_loads_with_errorid_guard', None)
    if parser is not None and not getattr(parser, '_rpc_evidence_patched', False):
        @functools.wraps(parser)
        def parse_reply(payload):
            obj = parser(payload)
            code = error_id(obj)
            if code is not None:
                obj['ErrorId'] = code
            trace = ACTIVE_TRACE.get()
            if trace is not None:
                trace.document(obj)
            return obj
        parse_reply._rpc_evidence_patched = True
        mod._json_loads_with_errorid_guard = parse_reply
    tq = getattr(mod, 'tq', None)
    if isinstance(tq, type) and not getattr(tq, '_kzz_patched', False):
        original = getattr(tq, 'get_kzz_info', None)
        if original is not None:
            def safe_get_kzz_info(cls, stock_code='', field_list=None):
                try:
                    return original(stock_code=stock_code, field_list=field_list or [])
                except IndexError:
                    logger.debug('SDK get_kzz_info empty result', exc_info=True)
                    return {}
            tq.get_kzz_info = classmethod(safe_get_kzz_info)
        tq._kzz_patched = True


def load_sdk(tdx_path: str):
    expected = (Path(tdx_path).expanduser() / 'PYPlugins' / 'sys' / 'tqcenter.py').resolve()
    if not expected.is_file():
        raise TdxInitError(f"通达信 TQ 插件不存在: {expected}")
    existing = sys.modules.get('tqcenter')
    if existing is not None:
        origin = getattr(existing, '__file__', None)
        if not origin or Path(origin).resolve() != expected:
            raise TdxInitError("进程已加载其他目录的 tqcenter；请使用新进程切换 SDK 路径")
        patch_module(existing)
        return existing
    added = []
    for directory in (expected.parent.parent, expected.parent):
        value = str(directory)
        if value not in sys.path:
            sys.path.insert(0, value)
            added.append(value)
    try:
        spec = importlib.util.spec_from_file_location('tqcenter', expected)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法创建模块加载器: {expected}")
        module = importlib.util.module_from_spec(spec)
        sys.modules['tqcenter'] = module
        with redirect_stdout(sys.stderr):
            spec.loader.exec_module(module)
        patch_module(module)
        if not hasattr(module, 'tq'):
            raise ImportError('SDK 未提供 tq 接口')
        return module
    except (Exception, SystemExit) as exc:
        sys.modules.pop('tqcenter', None)
        for value in added:
            sys.path.remove(value)
        raise TdxInitError(f"无法加载通达信 TQ 插件 {expected}: {exc}") from exc


class TqAdapter:
    def __init__(self, module):
        patch_module(module)
        self.module = module
        self.raw = module.tq

    def __getattr__(self, name):
        value = getattr(self.raw, name)
        if not callable(value):
            return value
        @functools.wraps(value)
        def call(*args, **kwargs):
            result, _ = self._invoke(name, value, args, kwargs)
            return result
        return call

    def read_with_metadata(self, name, **kwargs):
        """Return invocation-local batch metadata without changing raw SDK results."""
        if name not in BATCH_READ_METHODS:
            raise TdxParamError(f"{name} 不是受支持的批量读取接口")
        return self._invoke(name, getattr(self.raw, name), (), kwargs)

    def _invoke(self, name, value, args, kwargs):
        started = time.monotonic()
        patch_module(self.module)
        trace = RpcTrace(name)
        token = ACTIVE_TRACE.set(trace)
        try:
            dll = getattr(self.module, 'dll', None)
            native = dll is not None
            if native and name in SIDE_EFFECT_ENDPOINTS:
                endpoint = SIDE_EFFECT_ENDPOINTS[name][0]
                if endpoint not in dll._rpc_evidence_functions:
                    raise TdxInitError(f"SDK 缺少可核实的 {endpoint} 接口，拒绝发出操作")
            # A binding failure occurs before invoking the SDK, not after dispatch.
            if name in TRADE_METHODS:
                try:
                    inspect.signature(value).bind(*args, **kwargs)
                except TypeError as exc:
                    raise TdxParamError(f"{name} 调用参数不符合 SDK 签名") from exc
            with redirect_stdout(sys.stderr):
                result = value(*args, **kwargs)
            trace.check()
            if native and name in SIDE_EFFECT_ENDPOINTS and not trace.dispatched:
                raise TdxDataError(f"{name} 未发出可核实的操作请求")
            if native and name in READ_TYPES and not trace.confirmed_reply:
                raise TdxDataError(f"{name} 没有可核实的成功响应，不能解释为空数据")
            if name in READ_TYPES and trace.native_nonempty and not result:
                raise TdxDataError(f"{name} 丢失了原生非空结果，拒绝解释为空数据")
            checked = validate_result(result, name, require_data=name in REQUIRE_DATA,
                                      trade=name in TRADE_METHODS,
                                      account_unavailable=trace.account_unavailable)
            return checked, trace.read_metadata()
        except (Exception, SystemExit, KeyboardInterrupt) as exc:
            if isinstance(exc, TdxError):
                raise
            trace.check()
            if name in SIDE_EFFECT_ENDPOINTS and native and not trace.dispatched:
                if isinstance(exc, (ValueError, TypeError)):
                    raise TdxParamError(f"{name} 在发出请求前拒绝了参数") from exc
                raise TdxInitError(f"{name} 在发出请求前终止") from exc
            if name in TRADE_METHODS and (trace.dispatched or not native):
                raise TdxExecutionUnknownError(f"{name} 未取得确认回执；交易状态未知，请查询核对") from exc
            if name in ACK_METHODS and (trace.dispatched or not native):
                raise TdxOperationUnknownError(f"{name} 未取得确认回执；操作状态未知") from exc
            if isinstance(exc, TimeoutError):
                raise TdxReadTimeoutError(f"{name} 请求超时") from exc
            if isinstance(exc, SystemExit):
                raise TdxDataError(f"SDK 终止了 {name}") from exc
            raise
        finally:
            ACTIVE_TRACE.reset(token)
            logger.debug('sdk method=%s duration_ms=%.1f', name, (time.monotonic() - started) * 1000)

    def diagnostics(self) -> dict:
        return {'loaded_module_path': str(Path(self.module.__file__).resolve()),
                'dll_path': str(getattr(self.raw, 'dll_path', '')),
                'observed_native_functions': getattr(getattr(self.module, 'dll', None), '_rpc_evidence_functions', []),
                'methods': sorted(n for n in dir(self.raw) if not n.startswith('_') and callable(getattr(self.raw, n)))}
