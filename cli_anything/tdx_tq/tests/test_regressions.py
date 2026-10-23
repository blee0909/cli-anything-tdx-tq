"""Regression contracts for the foundation assessment; mutations use only a fake SDK."""
import sys, types, json, importlib, datetime
from pathlib import Path
import pytest
import pandas as pd
from click.testing import CliRunner

cli_module = importlib.import_module('cli_anything.tdx_tq.tdx_tq_cli')
session_module = importlib.import_module('cli_anything.tdx_tq.core.session')
from cli_anything.tdx_tq.utils.output import to_json
from cli_anything.tdx_tq.core.export import export_dataframe
from cli_anything.tdx_tq.core.config import Config

class FakeSDK:
    def __init__(self): self.calls=[]
    def stock_account(self): return 1
    def order_stock(self, **kw): self.calls.append(('order_stock',kw)); return -1
    def cancel_order_stock(self, **kw): self.calls.append(('cancel_order_stock',kw)); return -1
    def get_market_snapshot(self, code): return {'ErrorId':'1001','ErrorInfo':'not found'}
    def get_market_data(self, **kw): self.calls.append(('get_market_data',kw)); return {'close':pd.DataFrame({'600519.SH':[10.]},index=['20261020'])}
    def get_gb_info(self, **kw): self.calls.append(('get_gb_info',kw)); return []

class FakeSession:
    def __init__(self): self.tdx=FakeSDK()
    def ensure_initialized(self): pass
    def get_recent_trading_dates(self, count): return ['20261020']

@pytest.fixture
def fake(monkeypatch):
    s=FakeSession()
    monkeypatch.setattr(cli_module,'session',s)
    return s

def invoke(args): return CliRunner().invoke(cli_module.cli,args)

# Control checks establish that the harness reaches real implementation paths.
def test_control_daily_kline():
    out=cli_module._reformat_kline_data({'close':pd.DataFrame({'600519.SH':[10.]},index=['20261020'])})
    assert out['600519.SH']==[{'date':'20261020','close':10.}]

def test_control_preview_never_submits(fake):
    r=invoke(['trade','order','-s','600519.SH','-t','buy','-v','100','-p','10','--json'])
    assert r.exit_code==0 and json.loads(r.stdout)['status']=='preview'
    assert fake.tdx.calls==[]

def test_control_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv('TDX_PATH',raising=False)
    p=tmp_path/'config.json'
    c=Config(p); c.tdx_path='D:/example'; c.save()
    monkeypatch.delenv('TDX_PATH',raising=False)
    assert Config(p).tdx_path=='D:/example'

def test_control_common_error_json(fake):
    def fail(): raise RuntimeError('simulated disconnect')
    fake.ensure_initialized=fail
    r=invoke(['market','snapshot','-s','600519.SH','--json'])
    assert r.exit_code==1 and json.loads(r.stdout)['success'] is False

def test_minute_timestamps_remain_distinct():
    idx=pd.to_datetime(['2026-10-20 09:30:00','2026-10-20 09:31:00'])
    out=cli_module._reformat_kline_data({'close':pd.DataFrame({'600519.SH':[10.,11.]},index=idx)})
    assert len(set(x['date'] for x in out['600519.SH']))==2, out

def test_kline_preserves_stocks_from_all_fields():
    out=cli_module._reformat_kline_data({'open':pd.DataFrame({'A':[1.]},index=['d1']),'close':pd.DataFrame({'A':[2.],'B':[3.]},index=['d1'])})
    assert 'B' in out, out

def test_nested_dataframe_is_structured_json():
    value=json.loads(to_json({'A':pd.DataFrame({'close':[1.]})}))
    assert isinstance(value['A'],list), value

def test_json_excludes_nan():
    def reject(value): raise ValueError(value)
    json.loads(to_json({'price':float('nan')}),parse_constant=reject)

@pytest.mark.parametrize('command',[['stock','info','-s','600519.SH'],['financial','get','-s','600519.SH','-f','FN1'],['formula','zb','-n','MA']])
def test_json_errors_are_uniform(fake,command):
    def fail(): raise RuntimeError('simulated disconnect')
    fake.ensure_initialized=fail
    r=invoke(command+['--json'])
    assert r.exit_code==1
    assert json.loads(r.stdout)['success'] is False

@pytest.mark.parametrize('volume,price',[('-100','10'),('0','10'),('100','-1'),('100','nan'),('100','inf')])
def test_invalid_order_never_reaches_sdk(fake,volume,price):
    r=invoke(['trade','order','-s','600519.SH','-t','buy','-v',volume,'-p',price,'--confirm','--json'])
    assert fake.tdx.calls==[], {'calls':fake.tdx.calls,'output':r.stdout}
    assert r.exit_code!=0

def test_rejected_order_not_reported_success(fake):
    r=invoke(['trade','order','-s','600519.SH','-t','buy','-v','100','-p','10','--confirm','--json'])
    assert json.loads(r.stdout).get('success') is False, r.stdout
    assert r.exit_code!=0

def test_rejected_cancel_not_reported_success(fake):
    r=invoke(['trade','cancel','-o','fake-order','--json'])
    assert json.loads(r.stdout).get('success') is False, r.stdout
    assert r.exit_code!=0

def test_sdk_snapshot_error_is_failure(fake):
    r=invoke(['market','snapshot','-s','NO_SUCH_STOCK','--json'])
    assert r.exit_code!=0, r.stdout

def test_kline_sdk_error_not_lost(fake):
    fake.tdx.get_market_data=lambda **kw: {'error':-5,'msg':'unsupported period'}
    r=invoke(['market','get','-s','600519.SH','-p','tick','--json'])
    assert r.exit_code!=0,r.stdout

def test_calendar_fallback_does_not_invent_weekend_dates(monkeypatch):
    s=session_module.Session()
    monkeypatch.setattr(s,'ensure_initialized',lambda:None)
    monkeypatch.setattr(s,'_tdx',types.SimpleNamespace(get_trading_dates=lambda **kw:[]))
    with pytest.raises(session_module.TdxDataError, match='交易日历不可用'):
        s.get_recent_trading_dates(count=7)

def test_share_capital_does_not_duplicate_dates(fake):
    invoke(['stock','gb','-s','600519.SH','-c','3','--json'])
    dates=fake.tdx.calls[0][1]['date_list']
    assert len(dates)==len(set(dates)),dates

def test_excel_export_works_with_declared_dependencies(tmp_path):
    export_dataframe(pd.DataFrame({'close':[1.]}),str(tmp_path/'out.xlsx'),format='excel')
    assert (tmp_path/'out.xlsx').exists()

def test_json_export_preserves_named_date_index(tmp_path):
    df=pd.DataFrame({'close':[1.]},index=pd.Index(['20261020'],name='date'))
    p=tmp_path/'out.json';export_dataframe(df,str(p),format='json')
    assert json.loads(p.read_text(encoding='utf-8'))[0]['date']=='20261020'

def test_json_and_file_output_remain_machine_readable(fake,tmp_path):
    r=invoke(['market','get','-s','600519.SH','--json','-o',str(tmp_path/'out.json')])
    assert r.exit_code==0
    json.loads(r.stdout)
