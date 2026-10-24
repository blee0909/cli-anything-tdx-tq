"""Recent event dates must not be inferred from intraday status or price moves."""
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.errors import TdxDataError
from cli_anything.tdx_tq.core.limits import get_limit_summary


S = '002242.SZ'
OTHER = '600519.SH'


class SDK:
    def __init__(self):
        self.current = {S: {'ZDTStatusNow': '0', 'TimeNow': '0'}, OTHER: {'ZDTStatusNow': '1'}}
        self.info = {
            S: {'HqDate': '20261023', 'ZTDate_Recent': '20261021', 'DTDate_Recent': '20250407'},
            OTHER: {'HqDate': '20261022', 'ZTDate_Recent': '0'},
        }
        self.calls = []

    def get_zdt_data(self, **kwargs):
        self.calls.append(('get_zdt_data', kwargs))
        return self.current

    def get_more_info(self, stock_code, field_list):
        self.calls.append(('get_more_info', {'stock_code': stock_code, 'field_list': field_list}))
        return self.info[stock_code]


def test_batch_dates_preserve_details_and_distinguish_missing_from_zero():
    sdk = SDK()
    result = get_limit_summary(sdk, [S, OTHER])
    assert list(result) == [S, OTHER]
    assert result[S]['ZDTStatusNow'] == '0'
    assert result[S]['last_limit_up_date'] == '2026-10-21'
    assert result[S]['last_limit_down_date'] == '2025-04-07'
    assert result[S]['history'] == {
        'scope': 'sdk_recent_events', 'documented_lookback_years': 2, 'outside_documented_window': [],
        'source': 'get_more_info', 'as_of': '2026-10-23',
        'limit_up_status': 'found', 'limit_down_status': 'found',
    }
    assert result[OTHER]['last_limit_up_date'] is None
    assert result[OTHER]['last_limit_down_date'] is None
    assert result[OTHER]['history']['limit_up_status'] == 'no_record'
    assert result[OTHER]['history']['limit_down_status'] == 'unavailable'
    assert sdk.current[S] == {'ZDTStatusNow': '0', 'TimeNow': '0'}
    assert sdk.calls == [
        ('get_zdt_data', {'stock_list': [S, OTHER]}),
        ('get_more_info', {'stock_code': S, 'field_list': ['HqDate', 'ZTDate_Recent', 'DTDate_Recent']}),
        ('get_more_info', {'stock_code': OTHER, 'field_list': ['HqDate', 'ZTDate_Recent', 'DTDate_Recent']}),
    ]


@pytest.mark.parametrize('value,status', [
    (0, 'no_record'), ('0', 'no_record'), ('00000000', 'no_record'),
    (None, 'unavailable'), ('', 'unavailable'),
])
def test_empty_date_is_explicit(value, status):
    sdk = SDK()
    sdk.info[S]['ZTDate_Recent'] = value
    result = get_limit_summary(sdk, [S])[S]
    assert result['last_limit_up_date'] is None
    assert result['history']['limit_up_status'] == status


@pytest.mark.parametrize('value', ['20260230', '20261024', '2026-10-21', True, [], 'garbage'])
def test_invalid_or_future_event_date_fails(value):
    sdk = SDK()
    sdk.info[S]['ZTDate_Recent'] = value
    with pytest.raises(TdxDataError):
        get_limit_summary(sdk, [S])


def test_missing_as_of_does_not_use_machine_date():
    sdk = SDK()
    del sdk.info[S]['HqDate']
    assert get_limit_summary(sdk, [S])[S]['history']['as_of'] is None


@pytest.mark.parametrize('as_of,event,outside', [
    ('20261023', '20150421', ['last_limit_up_date']),
    ('20261023', '20241023', []),
    ('20240229', '20220228', []),
])
def test_client_dates_outside_documented_window_are_preserved(as_of, event, outside):
    sdk = SDK()
    sdk.info[S] = {'HqDate': as_of, 'ZTDate_Recent': event, 'DTDate_Recent': '0'}
    result = get_limit_summary(sdk, [S])[S]
    assert result['last_limit_up_date'] == f'{event[:4]}-{event[4:6]}-{event[6:8]}'
    assert result['history']['outside_documented_window'] == outside


@pytest.mark.parametrize('current', [{}, [], {S: {}}, {S: []}, {OTHER: {'ZDTStatusNow': '1'}}])
def test_missing_intraday_data_fails(current):
    sdk = SDK()
    sdk.current = current
    with pytest.raises(TdxDataError):
        get_limit_summary(sdk, [S])


@pytest.mark.parametrize('info', [None, [], {'ErrorId': '9'}])
def test_history_response_errors_fail(info):
    sdk = SDK()
    sdk.info[S] = info
    with pytest.raises(TdxDataError):
        get_limit_summary(sdk, [S])


@pytest.mark.parametrize('as_json', [False, True])
def test_cli_outputs_dates_in_both_formats(monkeypatch, as_json):
    sdk = SDK()
    monkeypatch.setattr(cli, 'session', SimpleNamespace(tdx=sdk, ensure_initialized=lambda: None))
    result = CliRunner().invoke(cli.cli, ['market', 'zdt', '-s', S, *(['--json'] if as_json else [])])
    assert result.exit_code == 0, result.output
    assert '2026-10-21' in result.stdout and '2025-04-07' in result.stdout
    if as_json:
        assert json.loads(result.stdout)[S]['history']['scope'] == 'sdk_recent_events'
    else:
        assert '最近涨停日期=2026-10-21' in result.stdout


def test_cli_rejects_bad_code_before_connecting(monkeypatch):
    def connect():
        pytest.fail('invalid arguments must not connect to the client')
    monkeypatch.setattr(cli, 'session', SimpleNamespace(ensure_initialized=connect))
    result = CliRunner().invoke(cli.cli, ['market', 'zdt', '-s', '002242', '--json'])
    assert result.exit_code == 2
    assert json.loads(result.stdout)['error_code'] == 'INVALID_PARAMETER'


@pytest.mark.live
def test_live_limit_dates_match_client_fields():
    from cli_anything.tdx_tq.core.session import session
    session.ensure_initialized()
    result = get_limit_summary(session.tdx, [S])[S]
    info = session.tdx.get_more_info(stock_code=S, field_list=['ZTDate_Recent', 'DTDate_Recent'])
    for output, field in [('last_limit_up_date', 'ZTDate_Recent'), ('last_limit_down_date', 'DTDate_Recent')]:
        assert field in info, f'Client does not provide {field}'
        value = info[field]
        expected = None if value in ('0', 0, '00000000', None, '') else f'{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}'
        assert result[output] == expected
