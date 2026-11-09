"""Contract verification for user/tqcenter.py (v1 user_tpythclient SDK).

Tests profile detection, removal of fake-mock stubs, capability checks,
zero-intrusion fixes for print noise, multi-market code validation,
empty-pointer handling in refresh_cache, v1 submit_order, and session close safety.
"""
import ctypes
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from cli_anything.tdx_tq.core.config import Config, config
from cli_anything.tdx_tq.core.errors import (
    TdxDataError,
    TdxExecutionUnknownError,
    TdxInitError,
    TdxOperationUnknownError,
    TdxParamError,
    TdxUnsupportedCapabilityError,
)
from cli_anything.tdx_tq.core.protocol import (
    ACK_METHODS,
    NATIVE_FUNCTIONS,
    RpcTrace,
)
from cli_anything.tdx_tq.core.sdk import (
    KNOWN_OPTIONAL_SDK_METHODS,
    TqAdapter,
    load_sdk,
    patch_module,
)
from cli_anything.tdx_tq.core.session import Session, session
from cli_anything.tdx_tq.core.trading import OrderRequest, submit_order


V1_DLL_FUNCTIONS = frozenset({
    'InitConnect', 'CloseConnect', 'GetStockListInStr', 'GetHISDATsInStr',
    'GetCWDATAInStr', 'Register_DataTransferFunc', 'SubscribeGPData',
    'SubscribeHQDUpdate', 'SetNewOrder', 'GetSTOCKInStr', 'GetREPORTInStr',
    'SetResToMain', 'GetBlockListInStr', 'GetBlockStocksInStr',
    'GetTradeCalendarInStr', 'ReFreshCacheAll', 'ReFreshCacheKLine',
    'DownLoadFiles', 'UserBlockControl', 'GetProDataInStr', 'GetCBINFOInStr',
    'GetIPOINFOInStr', 'GetUserBlockInStr',
})


class FakeCFunction:
    """Mock DLL export supporting .restype assignment and custom return payloads."""

    def __init__(self, name: str, handler=None):
        self.name = name
        self.handler = handler
        self.restype = None
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.handler:
            return self.handler(*args, **kwargs)
        if self.name == 'InitConnect':
            return json.dumps({'ErrorId': '0', 'run_id': '1'}).encode('utf-8')
        if self.name == 'CloseConnect':
            return None
        if self.name == 'GetREPORTInStr':
            return json.dumps({
                'ErrorId': '0', 'Now': 100.0, 'Max': 110.0, 'Min': 90.0,
                'Buyp': [100.0], 'Buyv': [10], 'Sellp': [101.0], 'Sellv': [20]
            }).encode('utf-8')
        if self.name == 'SetNewOrder':
            return json.dumps({'ErrorId': '0', 'OrderSysID': '12345'}).encode('utf-8')
        if self.name == 'ReFreshCacheAll':
            return json.dumps({'ErrorId': '0', 'Msg': 'OK'}).encode('utf-8')
        if self.name == 'SubscribeHQDUpdate':
            return json.dumps({'ErrorId': '0', 'Msg': 'Subscribed'}).encode('utf-8')
        if self.name == 'UserBlockControl':
            return json.dumps({'ErrorId': '0', 'Msg': 'BlockOK'}).encode('utf-8')
        if self.name == 'GetCBINFOInStr':
            return json.dumps({'ErrorId': '0', 'Data': [{'Code': '113050', 'Name': '南银转债'}]}).encode('utf-8')
        if self.name == 'GetTradeCalendarInStr':
            return json.dumps({'ErrorId': '0', 'Value': ['20261102', '20261103', '20261104', '20261105', '20261106']}).encode('utf-8')
        return json.dumps({'ErrorId': '0'}).encode('utf-8')


class FakeCDLL:
    """Mock CDLL providing exactly the 23 native functions of TPythClient.dll."""

    def __init__(self):
        self._funcs = {}

    def get_func(self, name: str) -> FakeCFunction:
        if name not in self._funcs:
            self._funcs[name] = FakeCFunction(name)
        return self._funcs[name]

    def __getattr__(self, name: str):
        if name in V1_DLL_FUNCTIONS:
            if name not in self._funcs:
                self._funcs[name] = FakeCFunction(name)
            return self._funcs[name]
        raise AttributeError(f"FakeCDLL has no native function '{name}'")


@pytest.fixture
def v1_sdk(tmp_path, monkeypatch):
    """Load an isolated instance of user/tqcenter.py using FakeCDLL."""
    monkeypatch.delitem(sys.modules, 'tqcenter', raising=False)

    fake_dll = FakeCDLL()
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: fake_dll)

    real_source = Path(r'D:\TDX64\PYPlugins\user\tqcenter.py')
    if real_source.is_file():
        code = real_source.read_text(encoding='utf-8')
    else:
        pytest.skip(r"D:\TDX64\PYPlugins\user\tqcenter.py 不存在")

    dest_dir = tmp_path / 'PYPlugins' / 'user'
    dest_dir.mkdir(parents=True, exist_ok=True)
    tq_file = dest_dir / 'tqcenter.py'
    tq_file.write_text(code, encoding='utf-8')

    module = load_sdk(str(tmp_path))
    adapter = TqAdapter(module)
    adapter.initialize('test_user_conn')
    return types.SimpleNamespace(module=module, adapter=adapter, dll=fake_dll, path=tmp_path)


def test_profile_detection(v1_sdk):
    """Test SDK profile detection: user_tpythclient vs sys_unified."""
    # v1 SDK from user/tqcenter.py should be identified as user_tpythclient
    assert v1_sdk.adapter.profile == 'user_tpythclient'

    # Fake sys_unified SDK with GetTdxDataStr / formula_zb
    v2_mod = types.SimpleNamespace(
        dll=types.SimpleNamespace(
            GetTdxDataStr=lambda *a: b'{"ErrorId": "0"}',
            _rpc_evidence_functions=['GetTdxDataStr']
        ),
        tq=types.SimpleNamespace(formula_zb=lambda *a: {}),
        __file__=__file__
    )
    v2_adapter = TqAdapter(v2_mod)
    assert v2_adapter.profile == 'sys_unified'


def test_missing_v2_methods_raise_unsupported_and_never_return_fake_mocks(v1_sdk):
    """Missing v2 methods must raise TdxUnsupportedCapabilityError and never return fake data."""
    adapter = v1_sdk.adapter

    # Verify capabilities dictionary and has_capability query
    assert adapter.has_capability('formula_xg') is False
    assert adapter.has_capability('get_zdt_data') is False
    assert adapter.has_capability('cancel_order_stock') is False
    assert adapter.has_capability('query_stock_positions') is False

    # Diagnostics metadata
    diag = adapter.diagnostics()
    assert diag['sdk_profile'] == 'user_tpythclient'
    assert diag['capabilities']['formula_xg'] is False
    assert diag['capabilities']['get_zdt_data'] is False
    assert diag['module_sha256'] is not None

    # Missing methods raise TdxUnsupportedCapabilityError
    with pytest.raises(TdxUnsupportedCapabilityError) as exc_info:
        adapter.formula_xg('MA')
    assert exc_info.value.code == "UNSUPPORTED_SDK_CAPABILITY"
    assert exc_info.value.details['method'] == 'formula_xg'
    assert exc_info.value.details['sdk_profile'] == 'user_tpythclient'

    with pytest.raises(TdxUnsupportedCapabilityError):
        adapter.get_zdt_data(['600519.SH'])

    with pytest.raises(TdxUnsupportedCapabilityError):
        adapter.cancel_order_stock(account_id=1, stock_code='600519.SH', order_id='123')

    with pytest.raises(TdxUnsupportedCapabilityError):
        adapter.query_stock_positions(account_id=1)

    # Random nonexistent attribute raises AttributeError
    with pytest.raises(AttributeError):
        getattr(adapter, 'nonexistent_foo_bar_method')


def test_snapshot_print_noise_is_suppressed(v1_sdk, capsys):
    """Snapshot print("json:", ...) noise is suppressed from stderr."""
    logging.getLogger('cli_anything.tdx_tq').setLevel(logging.INFO)
    adapter = v1_sdk.adapter

    # Calling get_market_snapshot
    res = adapter.get_market_snapshot(stock_code='600519.SH')
    assert res['ErrorId'] == '0'

    captured = capsys.readouterr()
    assert 'json:' not in captured.err
    assert 'json:' not in captured.out


def test_multimarket_codes_pass_validation_without_closing(v1_sdk):
    """Multi-market codes (HK, CFF) pass validation and do not call tq.close()."""
    mod = v1_sdk.module
    tq = mod.tq

    # Replace tq.close with a tracker to ensure it is never invoked during format checks
    closed = False
    orig_close = tq.close
    def tracked_close():
        nonlocal closed
        closed = True
    tq.close = tracked_close

    # Valid multi-market symbols
    assert mod.check_stock_code_format('00700.HK') is True
    assert mod.check_stock_code_format('IF2612.CFF') is True
    assert mod.check_stock_code_format('600519.SH') is True
    assert mod.check_stock_code_format(['00700.HK', '600519.SH']) is True
    assert closed is False

    # Invalid symbols return False but DO NOT call tq.close()
    assert mod.check_stock_code_format('12345.SH') is False  # A-share not 6 digits
    assert mod.check_stock_code_format('') is False
    assert closed is False

    tq.close = orig_close


def test_refresh_cache_empty_pointer_raises_cleanly(v1_sdk):
    """refresh_cache returning empty pointer raises cleanly without UnboundLocalError."""
    # Set ReFreshCacheAll DLL func to return empty pointer b""
    v1_sdk.dll.get_func('ReFreshCacheAll').handler = lambda *a, **kw: b""
    adapter = v1_sdk.adapter

    # Calling refresh_cache through adapter raises cleanly
    with pytest.raises((TdxOperationUnknownError, TdxDataError)):
        adapter.refresh_cache()


def test_submit_order_v1_signature_bridging(v1_sdk):
    """submit_order correctly invokes v1 order_stock signature (account, stock_code, ...)."""
    adapter = v1_sdk.adapter

    # Calling submit_order with string account ID
    req = OrderRequest(stock='600519.SH', direction='buy', volume=100, price=1800.0)
    result = submit_order(adapter, req, 'account_123', strategy_name='my_strat', order_remark='remark_test')

    assert result['ErrorId'] == '0'
    assert len(v1_sdk.dll.SetNewOrder.calls) == 1
    call_args, _ = v1_sdk.dll.SetNewOrder.calls[0]
    # In v1 user/tqcenter.py: SetNewOrder(run_id, account_str, code, order_type, order_volume, price_type, price, remark, timeout_ms)
    assert call_args[1] == b'account_123'
    assert call_args[2] == b'600519.SH'
    assert call_args[3] == 0  # buy
    assert call_args[4] == 100
    assert call_args[7] == b'remark_test'

    # Also supports integer account ID
    result2 = submit_order(adapter, req, 9988)
    assert result2['ErrorId'] == '0'


def test_session_close_detects_swallowed_close_failure(monkeypatch):
    """Session.close() detects when SDK swallows CloseConnect failure and raw._initialized stays True."""
    class FakeRawTq:
        def __init__(self):
            self._initialized = True
        def close(self):
            # Vendor SDK swallows error in _auto_close, leaving _initialized True
            pass

    fake_adapter = types.SimpleNamespace(
        raw=FakeRawTq(),
        close=lambda: None
    )

    s = Session()
    monkeypatch.setattr(s, '_tdx', fake_adapter)
    monkeypatch.setattr(s, '_initialized', True)
    monkeypatch.setattr(s, '_state', 'ready')

    with pytest.raises(TdxInitError, match="底层 SDK 未确认关闭连接"):
        s.close()

    assert s.is_initialized is False
    assert s.state == 'failed'


def test_cb_info_and_kzz_info_bridging(v1_sdk):
    """Both get_cb_info and get_kzz_info are supported, with IndexError safety and field filtering."""
    adapter = v1_sdk.adapter

    # Default fixture returns南银转债
    cb_data = adapter.get_cb_info(stock_code='113050.SH')
    assert cb_data.get('Name') == '南银转债'
    kzz_data = adapter.get_kzz_info(stock_code='113050.SH')
    assert kzz_data.get('Name') == '南银转债'

    # Case-insensitive field filtering
    filtered = adapter.get_cb_info(stock_code='113050.SH', field_list=['name'])
    assert 'Name' in filtered
    assert 'Code' not in filtered

    # Empty data list catches IndexError and returns {}
    v1_sdk.dll.get_func('GetCBINFOInStr').handler = lambda *a, **k: json.dumps({'ErrorId': '0', 'Data': []}).encode('utf-8')
    assert adapter.get_cb_info(stock_code='113050.SH') == {}
    assert adapter.get_kzz_info(stock_code='113050.SH') == {}


def test_unsubscribe_hq_string_error_raises_operation_unknown(v1_sdk):
    """unsubscribe_hq returning non-JSON plain Chinese error string raises TdxOperationUnknownError."""
    # When tqcenter's unsubscribe_hq encounters failure, it returns string "取消订阅...异常"
    v1_sdk.dll.get_func('SubscribeHQDUpdate').handler = lambda *a, **k: b""
    adapter = v1_sdk.adapter
    with pytest.raises(TdxOperationUnknownError):
        adapter.unsubscribe_hq(stock_list=['600519.SH'])


def test_get_recent_trading_dates_market_normalization(v1_sdk, monkeypatch):
    """get_recent_trading_dates normalizes AG to SH under user_tpythclient and mentions 999999 on empty."""
    s = Session()
    monkeypatch.setattr(s, '_tdx', v1_sdk.adapter)
    monkeypatch.setattr(s, '_loaded_path', str(Path(config.tdx_path).expanduser().resolve()))
    monkeypatch.setattr(s, '_initialized', True)
    monkeypatch.setattr(s, '_state', 'ready')

    recorded_market = []
    def fake_td(market='SH', count=5, **kw):
        recorded_market.append(market)
        return ['20261102', '20261103', '20261104', '20261105', '20261106']
    v1_sdk.adapter.raw.get_trading_dates = fake_td

    # market='AG' should be normalized to 'SH'
    dates = s.get_recent_trading_dates(count=3, market='AG')
    assert len(dates) == 3
    assert recorded_market[-1] == 'SH'

    # Empty trading calendar error mentions 上证指数(999999)盘后数据
    v1_sdk.adapter.raw.get_trading_dates = lambda **kw: []
    with pytest.raises(TdxDataError, match=r"上证指数\(999999\)盘后数据"):
        s.get_recent_trading_dates(count=3)


def test_config_discover_tdx_paths():
    """Config.discover_tdx_paths discovers local installation and reports plugin_type."""
    cfg = Config()
    discovered = cfg.discover_tdx_paths()
    assert isinstance(discovered, list)
    status = cfg.get_status()
    assert 'discovered_paths' in status
    assert status['discovered_paths'] == discovered

    # If D:\TDX64 exists, verify it is discovered as user
    if Path(r'D:\TDX64\PYPlugins\user\tqcenter.py').is_file():
        tdx64_entries = [d for d in discovered if d['path'].upper() == r'D:\TDX64']
        assert len(tdx64_entries) == 1
        assert tdx64_entries[0]['plugin_type'] == 'user'

