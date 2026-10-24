"""Adapter loading and fault contracts, without a native DLL."""
import sys
import types
from pathlib import Path

import pytest

from cli_anything.tdx_tq.core.errors import TdxAccountStateUnknownError, TdxDataError, TdxExecutionUnknownError, TdxInitError
from cli_anything.tdx_tq.core.sdk import TqAdapter, load_sdk, patch_module, validate_result


@pytest.mark.parametrize('value', [-1, False, {'ErrorId':'6'}, {'error':-5,'msg':'bad'}])
def test_sdk_failures_are_rejected(value):
    with pytest.raises(TdxDataError):
        validate_result(value,'example')


@pytest.mark.parametrize('value', [None, {}, [], ''])
def test_required_data_cannot_be_empty(value):
    with pytest.raises(TdxDataError):
        validate_result(value,'example',require_data=True)


@pytest.mark.parametrize('value', [None, {}, 1, {'order_id':'unconfirmed'}])
def test_trade_requires_explicit_sdk_success(value):
    with pytest.raises(TdxExecutionUnknownError):
        validate_result(value,'order_stock',trade=True)


def test_sdk_success_and_empty_optional_results():
    assert validate_result({'ErrorId':0},'order_stock',trade=True)=={'ErrorId':0}
    assert validate_result([],'optional')==[]
    assert validate_result(0,'optional')==0


@pytest.mark.parametrize('method,error', [('order_stock',TdxExecutionUnknownError),('cancel_order_stock',TdxExecutionUnknownError),('get_stock_info',TdxDataError)])
def test_adapter_timeout_semantics(method,error):
    def timeout(): raise TimeoutError('deadline')
    sdk=TqAdapter(types.SimpleNamespace(tq=types.SimpleNamespace(**{method:timeout})))
    with pytest.raises(error):getattr(sdk,method)()


def test_sdk_exit_becomes_structured_error():
    def stop(): raise SystemExit('SDK ErrorId=20')
    sdk=TqAdapter(types.SimpleNamespace(tq=types.SimpleNamespace(get_stock_info=stop)))
    with pytest.raises(TdxDataError): sdk.get_stock_info()


def create_module(tmp_path, code):
    path=tmp_path/'PYPlugins'/'sys'/'tqcenter.py'
    path.parent.mkdir(parents=True)
    path.write_text(code,encoding='utf-8')
    return path


def test_loader_uses_configured_source_and_rejects_switch(tmp_path,monkeypatch,capsys):
    monkeypatch.delitem(sys.modules,'tqcenter',raising=False)
    # Record the insertion for monkeypatch teardown, even when the loader later writes it.
    monkeypatch.setitem(sys.modules,'tqcenter',None)
    path=create_module(tmp_path,"print('SDK import noise')\nclass tq:\n run_id=-1\n dll_path='fake.dll'\n")
    module=load_sdk(str(tmp_path))
    assert Path(module.__file__)==path
    assert load_sdk(str(tmp_path)) is module
    assert capsys.readouterr().out==''
    info=TqAdapter(module).diagnostics()
    assert info['loaded_module_path']==str(path.resolve())
    other=tmp_path/'other'
    create_module(other,'class tq: pass')
    with pytest.raises(TdxInitError,match='新进程'):
        load_sdk(str(other))


@pytest.mark.parametrize('failure',["raise OSError('bad DLL')", "raise SystemExit('bad SDK')", 'value=1'])
def test_loader_faults_do_not_poison_imports(tmp_path,monkeypatch,failure):
    monkeypatch.setitem(sys.modules,'tqcenter',None)
    create_module(tmp_path,failure)
    before=list(sys.path)
    with pytest.raises(TdxInitError): load_sdk(str(tmp_path))
    assert 'tqcenter' not in sys.modules
    assert sys.path==before


def test_missing_sdk_path_is_explicit(tmp_path):
    with pytest.raises(TdxInitError,match='不存在'): load_sdk(str(tmp_path))


def test_gbk_patch_is_idempotent_and_preserves_errors(capsys):
    class DLL:
        def GetTdxDataStr(self): return '贵州'.encode('gbk')
    class TQ:
        @classmethod
        def get_kzz_info(cls,**kw): raise IndexError('empty')
    module=types.SimpleNamespace(dll=DLL(),tq=TQ)
    patch_module(module)
    wrapped=module.dll.GetTdxDataStr
    assert wrapped().decode('utf-8')=='贵州'
    patch_module(module)
    assert module.dll.GetTdxDataStr is wrapped
    assert module.tq.get_kzz_info()=={}
    module.print('diagnostic')
    capture=capsys.readouterr()
    assert capture.out=='' and 'diagnostic' in capture.err


def test_kzz_patch_does_not_hide_connection_fault():
    class TQ:
        @classmethod
        def get_kzz_info(cls,**kw):raise ConnectionError('disconnected')
    module=types.SimpleNamespace(tq=TQ)
    patch_module(module)
    with pytest.raises(ConnectionError): module.tq.get_kzz_info()


def test_account_sentinel_requires_protocol_evidence():
    with pytest.raises(TdxAccountStateUnknownError):
        validate_result(-1, 'stock_account')
    assert validate_result(-1, 'stock_account', account_unavailable=True) == -1


def test_retry_metadata_distinguishes_static_faults_from_reads():
    from cli_anything.tdx_tq.core.errors import TdxReadTimeoutError
    assert TdxInitError('missing DLL').retryable is False
    assert TdxReadTimeoutError('read timeout').retryable is True
    assert TdxExecutionUnknownError('submit timeout').retryable is False


def test_suffix_mappings_patched():
    mod = types.SimpleNamespace(
        SUPPORTED_STOCK_SUFFIXES={'SH', 'SZ'},
        MARKET_NUM_BY_SUFFIX={'SH': '1', 'SZ': '0'},
        SUPPORTED_MARKET_NUMBERS={'1', '0'}
    )
    patch_module(mod)
    for suffix, num in {'QHZ': '42', 'SSI': '68', 'SGE': '46', 'UZ': '70'}.items():
        assert suffix in mod.SUPPORTED_STOCK_SUFFIXES
        assert mod.MARKET_NUM_BY_SUFFIX[suffix] == num
        assert num in mod.SUPPORTED_MARKET_NUMBERS


def test_kline_zero_date_sanitization():
    class TQ:
        @classmethod
        def _fast_format_kline_data(cls, all_data, stock_list, fill_data):
            return all_data
    mod = types.SimpleNamespace(tq=TQ)
    patch_module(mod)
    raw = {
        'GDP.HG': {'Date': ['0'], 'Time': ['0'], 'Close': ['0.00']},
        '600519.SH': {'Date': ['20261023'], 'Time': ['0'], 'Close': ['1320.00']},
        'MIXED.SH': {'Date': ['0', '20261023'], 'Time': ['0', '0'], 'Close': ['0', '10.0']}
    }
    result = mod.tq._fast_format_kline_data(raw, list(raw.keys()), False)
    assert result['GDP.HG'] == {}
    assert result['600519.SH'] == raw['600519.SH']
    assert result['MIXED.SH']['Date'] == ['20261023']
    assert result['MIXED.SH']['Close'] == ['10.0']


def test_scjy_none_normalized_to_empty_dict():
    class TQ:
        @classmethod
        def get_scjy_value(cls, **kw):
            return None
        @classmethod
        def get_scjy_value_by_date(cls, **kw):
            return None
    mod = types.SimpleNamespace(tq=TQ)
    patch_module(mod)
    assert mod.tq.get_scjy_value() == {}
    assert mod.tq.get_scjy_value_by_date() == {}
    assert validate_result(None, 'get_scjy_value') == {}
    assert validate_result(None, 'get_scjy_value_by_date') == {}


def test_runtime_value_error_wrapped_as_data_error():
    def bad_parse(**kw):
        raise ValueError("time data '0' does not match format")
    sdk = TqAdapter(types.SimpleNamespace(tq=types.SimpleNamespace(get_market_data=bad_parse)))
    with pytest.raises(TdxDataError, match="运行时数据解析失败"):
        sdk.get_market_data()

