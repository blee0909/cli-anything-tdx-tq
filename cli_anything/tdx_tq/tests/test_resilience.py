"""Config, data and execution failure paths that must remain deterministic."""
import json
import types

import pandas as pd
import pytest

from cli_anything.tdx_tq.core.config import Config, config
from cli_anything.tdx_tq.core.errors import TdxConfigError, TdxDataError, TdxExecutionUnknownError, TdxInitError, TdxParamError
from cli_anything.tdx_tq.core.export import export_dataframe, export_dict
from cli_anything.tdx_tq.core.market import normalize_period, reformat_kline_data, stock_code, validate_dates
from cli_anything.tdx_tq.core.session import session
from cli_anything.tdx_tq.core.trading import OrderRequest, submit_order, cancel_order
from cli_anything.tdx_tq.utils.output import to_json


@pytest.mark.parametrize('content',['{', '[]','{}','{"tdx_path":123}','{"tdx_path":" "}'])
def test_invalid_config_is_diagnosed_not_silently_used(tmp_path,content):
    path=tmp_path/'bad.json';path.write_text(content)
    c=Config(path)
    assert c.get_status()['load_error']
    with pytest.raises(TdxConfigError):c.ensure_valid()
    c.override('D:/fixed');c.ensure_valid();c.save()
    assert Config(path).load_error is None


def test_config_precedence_and_environment_is_not_mutated(tmp_path,monkeypatch):
    path=tmp_path/'cfg.json';path.write_text('{"tdx_path":"D:/saved"}')
    c=Config(path)
    assert c.tdx_path=='D:/saved' and c.source=='file'
    monkeypatch.setenv('TDX_PATH','D:/env')
    assert c.tdx_path=='D:/env' and c.source=='environment'
    c.override('D:/cli')
    assert c.tdx_path=='D:/cli' and c.source=='command_line'
    assert __import__('os').environ['TDX_PATH']=='D:/env'


def test_failed_config_replace_preserves_original(tmp_path,monkeypatch):
    path=tmp_path/'cfg.json';path.write_text('{"tdx_path":"D:/original"}')
    c=Config(path);c.override('D:/new')
    def fail(*args):raise OSError('replace failed')
    monkeypatch.setattr('cli_anything.tdx_tq.core.config.os.replace',fail)
    with pytest.raises(OSError):c.save()
    assert json.loads(path.read_text())['tdx_path']=='D:/original'
    assert not list(tmp_path.glob('.config-*.tmp'))


def test_failed_export_does_not_replace_existing_file(tmp_path,monkeypatch):
    path=tmp_path/'out.csv';path.write_text('original')
    def fail(*args,**kw):raise OSError('disk full')
    monkeypatch.setattr(pd.DataFrame,'to_csv',fail)
    with pytest.raises(OSError):export_dataframe(pd.DataFrame({'x':[1]}),str(path))
    assert path.read_text()=='original'
    assert not list(tmp_path.glob('.export-*'))


@pytest.mark.parametrize('name',['out.xls','out.unknown'])
def test_export_extension_matches_format(tmp_path,name):
    with pytest.raises(ValueError):export_dataframe(pd.DataFrame({'x':[1]}),str(tmp_path/name),format='excel')


def test_export_dict_does_not_escape_output_directory(tmp_path):
    with pytest.raises(ValueError):export_dict({'../escape':pd.DataFrame({'x':[1]})},str(tmp_path))


@pytest.mark.parametrize('start,end',[('20260230',None),('2026',None),('20261021','20261020')])
def test_invalid_dates_rejected(start,end):
    with pytest.raises(TdxParamError):validate_dates(start,end)


def test_valid_dates_and_period_alias():
    validate_dates('20261020','20261021093000')
    assert normalize_period('60m')=='1h'
    with pytest.raises(TdxParamError):normalize_period('tick')
    with pytest.raises(TdxParamError):stock_code('invalid')


def test_kline_rejects_duplicate_time_and_preserves_union():
    with pytest.raises(TdxDataError):
        reformat_kline_data({'close':pd.DataFrame({'A':[1.,2.]},index=['d','d'])})
    value=reformat_kline_data({'open':pd.DataFrame({'A':[1.]},index=['d1']), 'close':pd.DataFrame({'A':[2.]},index=['d2'])})
    assert value['A']==[{'date':'d1','open':1.},{'date':'d2','close':2.}]


@pytest.mark.parametrize('volume,price,price_type,stock,direction',[
    (True,10.,0,'600519.SH','buy'),(100,0.,0,'600519.SH','buy'),
    (100,10.,3,'600519.SH','buy'),(100,10.,0,'','buy'),(100,10.,0,'600519.SH','hold'),
])
def test_order_validation_before_sdk(volume,price,price_type,stock,direction):
    with pytest.raises(TdxParamError):OrderRequest(stock,direction,volume,price,price_type).validate()


def test_trading_timeouts_and_missing_account_are_not_retried():
    calls=[]
    def timeout(**kw):calls.append(kw);raise TimeoutError('timeout')
    sdk=types.SimpleNamespace(order_stock=timeout,cancel_order_stock=timeout)
    request=OrderRequest('600519.SH','buy',100,10.)
    with pytest.raises(TdxExecutionUnknownError):submit_order(sdk,request,1)
    with pytest.raises(TdxExecutionUnknownError):cancel_order(sdk,1,'order')
    assert len(calls)==2
    with pytest.raises(TdxParamError):submit_order(sdk,request,-1)
    with pytest.raises(TdxParamError):cancel_order(sdk,1,' ')
    assert len(calls)==2


def test_session_calendar_uses_only_sdk_dates(monkeypatch):
    monkeypatch.setattr(session,'ensure_initialized',lambda:None)
    monkeypatch.setattr(session,'_tdx',types.SimpleNamespace(get_trading_dates=lambda **kw:['20261020','20261019','20261020']))
    assert session.get_recent_trading_dates(3)==['20261019','20261020']
    with pytest.raises(TdxParamError):session.get_recent_trading_dates(0)
    session._tdx.get_trading_dates=lambda **kw:['20261018']
    with pytest.raises(TdxDataError):session.get_recent_trading_dates()
    session._tdx.get_trading_dates=lambda **kw:['invalid']
    with pytest.raises(TdxDataError):session.get_recent_trading_dates()


def test_session_rejects_path_changes_and_cleans_failed_close(monkeypatch):
    config.override('D:/one')
    monkeypatch.setattr(session,'_loaded_path','D:/other')
    with pytest.raises(TdxInitError):session.initialize()
    def fail():raise RuntimeError('close failed')
    monkeypatch.setattr(session,'_tdx',types.SimpleNamespace(close=fail))
    monkeypatch.setattr(session,'_initialized',True)
    with pytest.raises(RuntimeError):session.close()
    assert session._initialized is False


def test_recursive_numpy_dates_and_missing_values():
    import numpy as np
    value=json.loads(to_json({'array':np.array([1.,np.inf]),'date':pd.Timestamp('2026-10-20'),
                              'series':pd.Series([pd.NA]),'nat':pd.NaT}))
    assert value['array']==[1.,None] and value['nat'] is None
    assert value['date']=='2026-10-20T00:00:00'
    with pytest.raises(TypeError):to_json(object())


@pytest.mark.parametrize('value', ['A.SH', '123.SZ', '1234567.BJ'])
def test_mainland_security_code_is_six_digits(value):
    with pytest.raises(TdxParamError): stock_code(value)

def test_stock_suffix_is_normalized():
    assert stock_code('600519.sh') == '600519.SH'
