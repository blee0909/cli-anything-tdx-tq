"""Command contracts exercised without a desktop client or real mutations."""
import datetime
import json
import types
from unittest.mock import Mock

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
    ['market','refresh'], ['market','refresh','-s','600519.SH','-p','5m'],
    ['stock','info','-s','600519.SH'], ['stock','more','-s','600519.SH'],
    ['stock','list'], ['stock','calendar'], ['stock','search','-k','茅台'],
    ['stock','gb','-s','600519.SH','-c','2'], ['stock','gb','-s','600519.SH','-d','20261020,20261019','-c','2'],
    ['stock','kzz','-s','113050.SH'],
    ['stock','relation','-s','600519.SH'],
    ['stock','ipo','--type','1','--date','0'],
    ['stock','etf','-z','000300.SH'],
    ['sector','list'], ['sector','stocks','-s','半导体'], ['sector','user'],
    ['sector','create','-c','TEST','-n','测试'], ['sector','delete','-c','TEST'],
    ['sector','rename','-c','TEST','-n','重命名'], ['sector','clear','-c','TEST'],
    ['sector','update-stocks','-c','TEST','-s','600519.SH'],
    ['financial','get','-s','600519.SH','-f','FN1'],
    ['financial','by-date','-s','600519.SH','-f','FN1'],
    ['financial','trading','--type','stock','--target','600519.SH','-f','GP1'],
    ['financial','trading','--type','sector','--target','880201.SH','-f','GP1'],
    ['financial','trading','--type','market','-f','SC1'],
    ['financial','one','-s','600519.SH','-f','GO1'],
    ['formula','list'], ['formula','info','-c','MA'], ['formula','zb','-n','MA'],
    ['formula','zb','-n','MA','-s','600519.SH'], ['formula','xg','-n','MA'],
    ['formula','xg','-n','MA','-s','600519.SH'],
    ['formula','exp','-n','BBI'],
    ['formula','exp','-n','BBI','-s','600519.SH'],
    ['client','link','-s','600519.SH'], ['client','warn','-s','600519.SH','-m','测试'],
    ['client','msg','-m','测试'],
    ['client','download','-s','688318.SH','--type','1'],
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
            'get_market_data': {
                'close': pd.DataFrame({'600519.SH':[1.]}, index=['20261020']),
                'open': pd.DataFrame({'600519.SH':[1.]}, index=['20261020']),
                'volume': pd.DataFrame({'600519.SH':[100.]}, index=['20261020']),
            },
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
            'get_gpjy_value_by_date':{'value':1}, 'get_bkjy_value_by_date':{'value':1}, 'get_scjy_value_by_date':{'value':1},
            'get_relation': [{'BlockCode': 'BK01', 'BlockName': '白酒'}],
            'get_ipo_info': [{'Code': '600519.SH', 'Name': '贵州茅台'}],
            'get_trackzs_etf_info': [{'Code': '510300.SH', 'Name': '300ETF'}],
            'get_gb_info_by_date': [{'Date': 20261020, 'Ltgb': 1.0, 'Zgb': 1.0}],
            'get_gp_one_data': {'600519.SH': {'GO1': '31.39', 'GO2': '7150.00'}},
            'formula_get_all':[{'acCode':'MA'}], 'formula_get_info':{'name':'MA'},
            'formula_zb':{'result':frame}, 'formula_process_mul_zb':{'result':frame},
            'formula_xg':['600519.SH'], 'formula_process_mul_xg':['600519.SH'],
            'formula_exp':{'result':frame}, 'formula_process_mul_exp':{'result':frame},
            'exec_to_tdx':1,'send_warn':1,'send_message':1,'stock_account':1,
            'download_file': json.dumps({'ErrorId': '0', 'Msg': 'success'}),
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


@pytest.mark.parametrize('sector,block_type', [
    ('半导体', 0), ('ZXG', 1), ('TJG', 1), ('CSBK', 1), ('IF', 2),
])
@pytest.mark.parametrize('list_option', ['--list-type', '--type', '-t'])
def test_sector_stocks_passes_independent_types(fake_session, sector, block_type, list_option):
    result = CliRunner().invoke(cli.cli, [
        'sector', 'stocks', '-s', sector, '--block-type', str(block_type),
        list_option, '1', '--json',
    ])
    assert result.exit_code == 0, result.output
    assert fake_session.raw.calls == [(
        'get_stock_list_in_sector', (),
        {'block_code': sector, 'block_type': block_type, 'list_type': 1},
    )]


def test_sector_stocks_defaults_preserve_plain_board_lookup(fake_session):
    result = CliRunner().invoke(cli.cli, ['sector', 'stocks', '-s', '半导体', '--json'])
    assert result.exit_code == 0, result.output
    assert fake_session.raw.calls[-1][2] == {
        'block_code': '半导体', 'block_type': 0, 'list_type': 0,
    }


@pytest.mark.parametrize('option,value', [
    ('--block-type', '-1'), ('--block-type', '3'),
    ('--list-type', '-1'), ('--list-type', '2'), ('--type', '2'),
])
def test_sector_stocks_invalid_types_fail_before_connection(fake_session, monkeypatch, option, value):
    initialize = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', initialize)
    result = CliRunner().invoke(cli.cli, [
        'sector', 'stocks', '-s', 'ZXG', option, value, '--json',
    ])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)['error_code'] == 'INVALID_PARAMETER'
    initialize.assert_not_called()
    assert not fake_session.raw.calls


@pytest.mark.parametrize('period', ['1h', '60m', '1w', '1mon', '45d', '1q', '1y', '10m', '15m', '30m'])
def test_refresh_invalid_period_fails_before_connection(fake_session, monkeypatch, period):
    initialize = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', initialize)
    result = CliRunner().invoke(cli.cli, [
        'market', 'refresh', '-s', '600519.SH', '-p', period, '--json',
    ])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)['error_code'] == 'INVALID_PARAMETER'
    initialize.assert_not_called()
    assert not fake_session.raw.calls


@pytest.mark.parametrize('period', ['1m', '5m', '1d', '1M', '5M', '1D'])
def test_refresh_supported_period_reaches_sdk(fake_session, period):
    result = CliRunner().invoke(cli.cli, [
        'market', 'refresh', '-s', '600519.SH', '-p', period, '--json',
    ])
    assert result.exit_code == 0, result.output
    assert fake_session.raw.calls == [(
        'refresh_kline', (), {'stock_list': ['600519.SH'], 'period': period.lower()},
    )]


@pytest.mark.parametrize('period', ['1h', '60m', '1w', '1mon', '45d', '1q', '1y'])
def test_market_get_keeps_periods_unsupported_by_refresh(fake_session, period):
    result = CliRunner().invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '-p', period, '--json',
    ])
    assert result.exit_code == 0, result.output
    assert fake_session.raw.calls[-1][0] == 'get_market_data'
    assert fake_session.raw.calls[-1][2]['period'] == ('1h' if period == '60m' else period)


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
    ['financial','trading','--type','stock','-f','GP01','--json'],
    ['financial','trading','--type','sector','-f','BK05','--json'],
    ['financial','trading','--type','stock','--target','600519.SH','-f','GP01','--start','20261301','--json'],
    ['sector','list','-c','invalid_category','--json'],
    ['sector','list','-t','2','--json'],
    ['stock','ipo','-t','5','--json'],
    ['stock','relation','--json'],
    ['stock','etf','--json'],
    ['financial','one','-s','600519.SH','--json'],
    ['client','download','-s','600519.SH','-t','10','--json'],
    ['stock','gb','-s','600519.SH','--start','20261020','-d','20261019','--json'],
    ['stock','gb','-s','600519.SH','--start','20261020','-c','5','--json'],
    ['financial','trading','--type','stock','--target','600519.SH','-f','GP01','--start','20261020','--year','2024','--json'],
])
def test_click_usage_errors_are_json(args):
    result=CliRunner().invoke(cli.cli,args)
    assert result.exit_code==2
    assert json.loads(result.stdout)['error_code']=='INVALID_PARAMETER'


def test_sector_list_dispatch_by_category(fake_session):
    result = CliRunner().invoke(cli.cli, ['sector', 'list', '-c', 'industry', '-t', '1', '--json'])
    assert result.exit_code == 0
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'get_stock_list'
    assert kwargs == {'market': '11', 'list_type': 1}

    result = CliRunner().invoke(cli.cli, ['sector', 'list', '-c', 'all', '-t', '0', '--json'])
    assert result.exit_code == 0
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'get_sector_list'
    assert kwargs == {'list_type': 0}


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


def test_market_get_and_snapshot_fields(fake_session):
    result = CliRunner().invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '-f', 'open,volume', '-c', '2', '--json'])
    assert result.exit_code == 0
    assert fake_session.raw.calls[-1][2]['field_list'] == ['open', 'volume']

    result = CliRunner().invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH', '-f', 'Open,Close', '--json'])
    assert result.exit_code == 0
    assert fake_session.raw.calls[-1][2]['field_list'] == ['Open', 'Close']


def test_stock_info_and_kzz_fields(fake_session):
    result = CliRunner().invoke(cli.cli, ['stock', 'info', '-s', '600519.SH', '-f', 'Name,Unit', '--json'])
    assert result.exit_code == 0
    assert fake_session.raw.calls[-1][2]['field_list'] == ['Name', 'Unit']

    result = CliRunner().invoke(cli.cli, ['stock', 'kzz', '-s', '113050.SH', '-f', 'ZGPRICE', '--json'])
    assert result.exit_code == 0
    assert fake_session.raw.calls[-1][2]['field_list'] == ['ZGPRICE']


def test_stock_gb_date_interval_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, ['stock', 'gb', '-s', '600519.SH', '--start', '20260101', '--end', '20261020', '--json'])
    assert result.exit_code == 0
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'get_gb_info_by_date'
    assert kwargs == {'stock_code': '600519.SH', 'start_date': '20260101', 'end_date': '20261020'}


def test_financial_get_report_type_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, ['financial', 'get', '-s', '600519.SH', '-f', 'FN1', '--report-type', 'announce_time', '--json'])
    assert result.exit_code == 0
    assert fake_session.raw.calls[-1][2]['report_type'] == 'announce_time'


def test_financial_trading_by_date_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, ['financial', 'trading', '-t', 'stock', '--target', '600519.SH', '-f', 'GP01', '--year', '2024', '--mmdd', '630', '--json'])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'get_gpjy_value_by_date'
    assert kwargs == {'stock_list': ['600519.SH'], 'field_list': ['GP01'], 'year': 2024, 'mmdd': 630}

    result = CliRunner().invoke(cli.cli, ['financial', 'trading', '-t', 'sector', '--target', 'BK01', '-f', 'BK05', '--year', '2024', '--mmdd', '630', '--json'])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'get_bkjy_value_by_date'
    assert kwargs == {'stock_list': ['BK01'], 'field_list': ['BK05'], 'year': 2024, 'mmdd': 630}

    result = CliRunner().invoke(cli.cli, ['financial', 'trading', '-t', 'market', '-f', 'SC01', '--year', '2024', '--mmdd', '630', '--json'])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'get_scjy_value_by_date'
    assert kwargs == {'field_list': ['SC01'], 'year': 2024, 'mmdd': 630}


def test_financial_one_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, ['financial', 'one', '-s', '600519.SH', '-f', 'GO1,GO2', '--json'])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'get_gp_one_data'
    assert kwargs == {'stock_list': ['600519.SH'], 'field_list': ['GO1', 'GO2']}


def test_formula_options_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, [
        'formula', 'zb', '-n', 'MACD', '-s', '600519.SH',
        '--return-count', '0', '--return-date', '--xsflag', '2', '--dividend', '1',
        '--start', '20260101', '--end', '20261020', '--json'
    ])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'formula_process_mul_zb'
    assert kwargs['return_count'] == 0
    assert kwargs['return_date'] is True
    assert kwargs['xsflag'] == 2
    assert kwargs['dividend_type'] == 1
    assert kwargs['start_time'] == '20260101'
    assert kwargs['end_time'] == '20261020'

    result = CliRunner().invoke(cli.cli, [
        'formula', 'exp', '-n', 'BBI', '-s', '600519.SH',
        '--return-count', '2', '--xsflag', '4', '--json'
    ])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'formula_process_mul_exp'
    assert kwargs['return_count'] == 2
    assert kwargs['xsflag'] == 4


def test_client_download_dispatch(fake_session):
    result = CliRunner().invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '2', '--date', '20261020', '--json'
    ])
    assert result.exit_code == 0
    name, _, kwargs = fake_session.raw.calls[-1]
    assert name == 'download_file'
    assert kwargs == {'stock_code': '688318.SH', 'down_time': '20261020', 'down_type': 2}
