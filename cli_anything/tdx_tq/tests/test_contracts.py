"""Command contracts exercised without a desktop client or real mutations."""
import datetime
import json
import types

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.sdk import TqAdapter

COMMANDS = [
    ['market','get','-s','600519.SH','-c','2'],
    ['market','snapshot','-s','600519.SH,000001.SZ'],
    ['market','divid','-s','600519.SH'],
    ['market','zdt','-s','600519.SH'],
    ['market','exday','-s','600519.SH'],
    ['market','pv','-s','600519.SH'],
    ['market','refresh'], ['market','refresh','-s','600519.SH','-p','60m'],
    ['stock','info','-s','600519.SH'], ['stock','more','-s','600519.SH'],
    ['stock','list'], ['stock','calendar'], ['stock','search','-k','茅台'],
    ['stock','gb','-s','600519.SH','-c','2'], ['stock','gb','-s','600519.SH','-d','20261020,20261019','-c','2'],
    ['stock','kzz','-s','113050.SH'],
    ['sector','list'], ['sector','stocks','-s','半导体'], ['sector','user'],
    ['sector','create','-c','TEST','-n','测试'], ['sector','delete','-c','TEST'],
    ['sector','rename','-c','TEST','-n','重命名'], ['sector','clear','-c','TEST'],
    ['sector','update-stocks','-c','TEST','-s','600519.SH'],
    ['financial','get','-s','600519.SH','-f','FN1'],
    ['financial','by-date','-s','600519.SH','-f','FN1'],
    ['financial','trading','--type','stock','--target','600519.SH','-f','GP1'],
    ['financial','trading','--type','sector','--target','880201.SH','-f','GP1'],
    ['financial','trading','--type','market','-f','SC1'],
    ['formula','list'], ['formula','info','-c','MA'], ['formula','zb','-n','MA'],
    ['formula','zb','-n','MA','-s','600519.SH'], ['formula','xg','-n','MA'],
    ['formula','xg','-n','MA','-s','600519.SH'],
    ['client','link','-s','600519.SH'], ['client','warn','-s','600519.SH','-m','测试'],
    ['client','msg','-m','测试'],
    ['trade','account'], ['trade','asset'], ['trade','positions'], ['trade','orders'],
    ['trade','order','-s','600519.SH','-t','buy','-v','100','-p','10','--confirm'],
    ['trade','cancel','-o','fake-order'],
]


class SDK:
    run_id = 1
    dll_path = 'fake.dll'

    def __init__(self):
        self.calls = []
        self.failure = False

    def __getattr__(self, name):
        frame = pd.DataFrame({'FN1':[1.]}, index=pd.DatetimeIndex(['2026-10-20'], name='date'))
        responses = {
            'get_market_data': {'close': pd.DataFrame({'600519.SH':[1.]}, index=['20261020'])},
            'get_market_snapshot': {'ErrorId':'0','Now':'1.0'}, 'get_divid_factors':frame,
            'get_zdt_data': {'600519.SH':{'up':2}}, 'get_exday_data':[{'date':'20261020'}],
            'get_pricevol': {'volume':1}, 'refresh_cache':1, 'refresh_kline':1,
            'get_stock_info': {'Name':'贵州茅台'}, 'get_more_info': {'value':1},
            'get_stock_list':['600519.SH'], 'get_trading_dates':['20261019','20261020'],
            'get_match_stkinfo':[{'Code':'600519.SH','Name':'贵州茅台'}], 'get_gb_info':[{'Ltgb':1}],
            'get_kzz_info':{'name':'test'}, 'get_sector_list':['半导体'],
            'get_stock_list_in_sector':['600519.SH'], 'get_user_sector':['自选'],
            'create_sector':1,'delete_sector':1,'rename_sector':1,'clear_sector':1,'send_user_block':1,
            'get_financial_data':{'600519.SH':frame}, 'get_financial_data_by_date':{'600519.SH':{'FN1':1}},
            'get_gpjy_value':{'value':1}, 'get_bkjy_value':{'value':1}, 'get_scjy_value':{'value':1},
            'formula_get_all':[{'acCode':'MA'}], 'formula_get_info':{'name':'MA'},
            'formula_zb':{'result':frame}, 'formula_process_mul_zb':{'result':frame},
            'formula_xg':['600519.SH'], 'formula_process_mul_xg':['600519.SH'],
            'exec_to_tdx':1,'send_warn':1,'send_message':1,'stock_account':1,
            'query_stock_asset':{'cash':1}, 'query_stock_positions':[{'code':'600519.SH'}],
            'query_stock_orders':[{'order_id':'fake'}],
            'order_stock':{'ErrorId':'0','order_id':'fake'}, 'cancel_order_stock':{'ErrorId':'0'},
        }
        from cli_anything.tdx_tq.core.protocol import ACK_METHODS
        for method in ACK_METHODS:
            responses[method] = {'ErrorId': '0'}
        if name not in responses:
            raise AttributeError(name)
        def call(*args, **kwargs):
            self.calls.append((name,args,kwargs))
            if self.failure:
                raise RuntimeError('simulated failure')
            return responses[name]
        return call


class Session:
    def __init__(self):
        self.raw = SDK()
        self.tdx = TqAdapter(types.SimpleNamespace(tq=self.raw,__file__=__file__))
        self.is_initialized = False
    def ensure_initialized(self): self.is_initialized=True
    def initialize(self): self.ensure_initialized()
    def close(self): self.is_initialized=False
    def get_recent_trading_dates(self,count): return ['20261019','20261020'][-count:]
    def diagnostics(self): return self.tdx.diagnostics()


@pytest.fixture
def fake_session(monkeypatch):
    session = Session()
    monkeypatch.setattr(cli,'session',session)
    return session


@pytest.mark.parametrize('args', COMMANDS)
@pytest.mark.parametrize('as_json', [True, False])
def test_command_success_contract(args, as_json, fake_session):
    result=CliRunner().invoke(cli.cli, args+(['--json'] if as_json else []))
    assert result.exit_code==0, result.output
    assert result.stdout.strip()
    if as_json:
        json.loads(result.stdout, parse_constant=lambda v: pytest.fail(f'Nonfinite JSON: {v}'))


@pytest.mark.parametrize('args', COMMANDS)
def test_command_failure_is_json(args, fake_session):
    fake_session.raw.failure=True
    result=CliRunner().invoke(cli.cli,args+['--json'])
    assert result.exit_code!=0, result.output
    error=json.loads(result.stdout)
    assert error['success'] is False
    assert error['error_code'] and error['error_type']
    assert isinstance(error['retryable'],bool)


@pytest.mark.parametrize('args', [['init'],['close'],['status'],['config','show']])
@pytest.mark.parametrize('as_json',[True,False])
def test_local_commands_without_sdk(args,as_json):
    # status/config/close must not import or initialize tqcenter.
    if args==['init']:
        args=['--tdx-path','missing-sdk',*args]
    result=CliRunner().invoke(cli.cli,args+(['--json'] if as_json else []))
    assert result.exit_code==(1 if 'init' in args else 0),result.output
    if as_json:
        json.loads(result.stdout)


@pytest.mark.parametrize('args', [
    ['unknown','--json'], ['stock','info','--json'],
    ['market','get','-s','600519.SH','-c','0','--json'],
    ['trade','order','-s','600519.SH','-t','hold','-v','100','--json'],
])
def test_click_usage_errors_are_json(args):
    result=CliRunner().invoke(cli.cli,args)
    assert result.exit_code==2
    assert json.loads(result.stdout)['error_code']=='INVALID_PARAMETER'


def test_financial_dates_and_nested_formula_are_structured(fake_session):
    result=CliRunner().invoke(cli.cli,['financial','get','-s','600519.SH','-f','FN1','--json'])
    assert json.loads(result.stdout)['600519.SH'][0]['date']=='2026-10-20T00:00:00'
    result=CliRunner().invoke(cli.cli,['formula','zb','-n','MA','--json'])
    assert json.loads(result.stdout)['result'][0]['FN1']==1


def test_price_type_and_direction_mapping(fake_session):
    result=CliRunner().invoke(cli.cli,['trade','order','-s','600519.SH','-t','sell','-v','100','-p','0','--price-type','1','--confirm','--json'])
    assert result.exit_code==0,result.output
    name,_,request=fake_session.raw.calls[-1]
    assert name=='order_stock'
    assert request==dict(account_id=1,stock_code='600519.SH',order_type=1,order_volume=100,price_type=1,price=0.)
    assert json.loads(result.stdout)['status']=='accepted'


def test_preview_needs_no_sdk(fake_session):
    fake_session.raw.failure=True
    result=CliRunner().invoke(cli.cli,['trade','order','-s','600519.SH','-t','buy','-v','100','-p','10','--json'])
    assert result.exit_code==0
    assert not fake_session.raw.calls
    assert json.loads(result.stdout)['order']['account_id'] is None


def test_warning_uses_full_sdk_timestamp(fake_session):
    result=CliRunner().invoke(cli.cli,['client','warn','-s','600519.SH','-m','测试','--json'])
    assert result.exit_code==0
    stamp=fake_session.raw.calls[-1][2]['time_list'][0]
    datetime.datetime.strptime(stamp,'%Y%m%d%H%M%S')


def test_config_set_isolated_and_json(tmp_path):
    result=CliRunner().invoke(cli.cli,['config','set','--tdx-path','D:/new-tdx','--json'])
    assert result.exit_code==0,result.output
    data=json.loads(result.stdout)
    assert json.loads((tmp_path/'config.json').read_text())['tdx_path']=='D:/new-tdx'
    assert data['success'] is True
