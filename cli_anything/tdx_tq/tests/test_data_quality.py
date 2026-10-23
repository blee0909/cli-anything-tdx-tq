"""Regression cases for date modes, usable batch data and native success codes."""
import json
import types

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.errors import TdxDataError, TdxIncompleteDataError, TdxParamError
from cli_anything.tdx_tq.core.market import MarketRequest, date_bound, reformat_kline_data
from cli_anything.tdx_tq.core.protocol import ACTIVE_TRACE
from cli_anything.tdx_tq.core.quality import assess_financial, assess_market, parse_fields, read_batch
from cli_anything.tdx_tq.core.sdk import TqAdapter
from cli_anything.tdx_tq.tests.test_contracts import Session
from cli_anything.tdx_tq.tests.test_protocol import make_sdk, reply
from cli_anything.tdx_tq.utils.cli import error_payload
from cli_anything.tdx_tq.utils.output import to_json

S = '600519.SH'
OTHER = '999999.SZ'


def invoke(monkeypatch, args, *, market=None, financial=None, method='get_financial_data'):
    fake = Session()
    if market is not None:
        fake.raw.get_market_data = lambda **kwargs: market
    if financial is not None:
        setattr(fake.raw, method, lambda **kwargs: financial)
    initialized = []
    fake.ensure_initialized = lambda: initialized.append(True)
    monkeypatch.setattr(cli, 'session', fake)
    result = CliRunner().invoke(cli.cli, [*args, '--json'])
    return result, initialized, fake


@pytest.mark.parametrize('count', [None, 1, 75])
def test_tail_request_keeps_end_of_day_and_never_sends_start(count):
    params = MarketRequest('60m', count, end='20261020').sdk_kwargs()
    assert params == dict(period='1h', count=count or 60, start_time='',
                          end_time='20261020235959', fill_data=False)


def test_range_has_no_implicit_sixty_record_limit():
    params = MarketRequest(start='20260101', end='20261020').sdk_kwargs()
    assert params['count'] == -1 and params['start_time'] == '20260101'
    dates = pd.date_range('2026-01-01', periods=100)
    MarketRequest(start='20260101', end='20261020').validate_output({S: [{'date': d.isoformat()} for d in dates]})


@pytest.mark.parametrize('start,end', [('20261020153000', '20261020'), ('20261020', '20261020153000')])
def test_different_date_precisions_compare_as_times(start, end):
    MarketRequest(start=start, end=end)


@pytest.mark.parametrize('value', ['', '2026-10-20', '2026102', '20260230', '20261020250000', 'abcdefgh'])
def test_dates_have_strict_shape_and_calendar_validation(value):
    with pytest.raises(TdxParamError):
        date_bound(value)


@pytest.mark.parametrize('args', [
    ['market','get','-s',S,'--start','20261020','-c','3'],
    ['market','get','-s',S,'--start','20261021','--end','20261020'],
    ['financial','get','-s',S,'-f',' , '],
    ['financial','get','-s',S,'-f','FN1','--start','20260230'],
    ['financial','by-date','-s',S,'-f',''],
    ['financial','by-date','-s',S,'-f','FN1','--year','-1'],
    ['financial','by-date','-s',S,'-f','FN1','--mmdd','101'],
])
def test_invalid_requests_never_connect(monkeypatch, args):
    result, initialized, _ = invoke(monkeypatch, args)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert initialized == []


@pytest.mark.parametrize('stamp', ['20261019', '20261021000000', 'bad', None])
def test_sdk_cannot_return_dates_outside_interval(stamp):
    with pytest.raises(TdxDataError):
        MarketRequest(start='20261020', end='20261020').validate_output({S:[{'date':stamp}]})


def test_interval_query_reaches_sdk_without_count_mode(monkeypatch):
    result, _, fake = invoke(monkeypatch, ['market','get','-s',S,'--start','20261020','--end','20261020'])
    assert result.exit_code == 0, result.output
    params = fake.raw.calls[-1][2]
    assert params['count'] == -1 and params['end_time'] == '20261020235959'
    assert params['fill_data'] is False


@pytest.mark.parametrize('value', [None, pd.NA, float('nan'), float('inf')])
def test_key_presence_without_close_is_not_available(value):
    batch = assess_market({S:[{'date':'20261020','close':10.}], OTHER:[{'date':'20261020','close':value}]}, [S,OTHER])
    with pytest.raises(TdxIncompleteDataError) as error:
        batch.require()
    payload = json.loads(to_json(error_payload(error.value)))
    assert payload['error_code'] == 'DATA_INCOMPLETE'
    assert payload['details']['missing_stocks'] == [OTHER]
    assert list(payload['data']) == [S]
    assert batch.require(True).metadata['partial'] is True


def test_zero_prices_are_data_and_rows_without_close_are_reported():
    batch = assess_market({S:[{'close':0.}, {'close':None}]}, [S]).require(True)
    assert batch.data[S] == [{'close':0.}]
    assert batch.metadata['dropped_records'] == {S:1}
    assert batch.metadata['record_counts'] == {S:1}


def test_missing_and_unrequested_symbols_are_reported():
    batch = assess_market({S:[{'close':1}], 'EXTRA':[]}, [S,OTHER]).require(True)
    assert batch.metadata['missing_stocks'] == [OTHER]
    assert batch.metadata['unexpected_stocks'] == ['EXTRA']


@pytest.mark.parametrize('allow', [False, True])
def test_no_data_is_failure_even_when_partial_is_allowed(allow):
    with pytest.raises(TdxIncompleteDataError):
        assess_market({S:[{'close':None}]}, [S]).require(allow)


@pytest.mark.parametrize('bad_index', [pd.DatetimeIndex([pd.NaT]), pd.Index([None])])
def test_missing_market_time_is_sdk_data_error(bad_index):
    with pytest.raises(TdxDataError):
        reformat_kline_data({'Close':pd.DataFrame({S:[1.]}, index=bad_index)})


def test_colliding_market_field_names_are_not_silently_overwritten():
    frame = pd.DataFrame({S:[1.]}, index=['20261020'])
    with pytest.raises(TdxDataError):
        reformat_kline_data({'Close':frame, 'close':frame})


def test_invalid_sdk_number_is_not_a_user_parameter_error(monkeypatch):
    result, _, _ = invoke(monkeypatch, ['market','get','-s',S],
                          market={'Close':pd.DataFrame({S:['bad']}, index=['20261020'])})
    assert result.exit_code == 1
    assert json.loads(result.stdout)['error_code'] == 'SDK_DATA_ERROR'


def test_strict_partial_export_does_not_replace_existing_file(monkeypatch, tmp_path):
    target = tmp_path / 'bars.json'
    target.write_text('original', encoding='utf8')
    raw = {'Close':pd.DataFrame({S:[1.],OTHER:[None]}, index=['20261020'])}
    args = ['market','get','-s',S+','+OTHER,'-o',str(target)]
    result, _, _ = invoke(monkeypatch, args, market=raw)
    assert result.exit_code == 1 and target.read_text(encoding='utf8') == 'original'
    result, _, _ = invoke(monkeypatch, [*args,'--allow-partial'], market=raw)
    envelope = json.loads(result.stdout)
    assert result.exit_code == 0 and envelope['partial'] is True
    assert envelope['records'] == 1 and envelope['missing_stocks'] == [OTHER]
    exported = json.loads(target.read_text(encoding='utf8'))
    assert len(exported) == 1 and exported[0]['stock'] == S


def test_allow_partial_envelope_is_stable_even_for_complete_data(monkeypatch):
    result, _, _ = invoke(monkeypatch, ['market','get','-s',S,'--allow-partial'])
    envelope = json.loads(result.stdout)
    assert result.exit_code == 0 and envelope['schema_version'] == 1
    assert envelope['partial'] is False and envelope['available_stocks'] == [S]


@pytest.mark.parametrize('value', [None, '', '  ', '--', pd.NA, pd.NaT, float('nan'), float('inf')])
@pytest.mark.parametrize('as_frame', [False, True])
def test_financial_sentinels_do_not_count_as_requested_data(value, as_frame):
    item = pd.DataFrame({'FN1':[value]}) if as_frame else {'FN1':value}
    batch = assess_financial({S:item}, [S], ['FN1'])
    with pytest.raises(TdxIncompleteDataError):
        batch.require(True)
    assert batch.metadata['missing_fields'] == {S:['FN1']}


@pytest.mark.parametrize('value', [0, 0., '0', '123.4'])
def test_financial_zero_and_numeric_strings_are_valid(value):
    batch = assess_financial({S:{'fn1':value}}, [S], ['FN1']).require()
    assert batch.data[S]['fn1'] == value


def test_financial_requested_field_is_required_beyond_metadata():
    batch = assess_financial({S:{'announce_time':None,'tag_time':None}}, [S], ['FN1'])
    with pytest.raises(TdxIncompleteDataError):
        batch.require()
    assert batch.metadata['available_stocks'] == []
    assert batch.metadata['missing_fields'] == {S:['FN1']}


@pytest.mark.parametrize('method,command', [('get_financial_data','get'), ('get_financial_data_by_date','by-date')])
def test_financial_cli_exposes_partial_fields(monkeypatch, method, command):
    args = ['financial',command,'-s',S,'-f','FN1,FN4']
    result, _, _ = invoke(monkeypatch, args, financial={S:{'FN1':1}}, method=method)
    payload = json.loads(result.stdout)
    assert result.exit_code == 1 and payload['details']['missing_fields'] == {S:['FN4']}
    result, _, _ = invoke(monkeypatch, [*args,'--allow-partial'], financial={S:{'FN1':1}}, method=method)
    payload = json.loads(result.stdout)
    assert result.exit_code == 0 and payload['partial'] is True and payload['data'][S]['FN1'] == 1


@pytest.mark.parametrize('as_frame', [False,True])
def test_partial_cells_are_null_and_empty_rows_are_removed_without_mutation(as_frame):
    item = {'FN1':[1,'--',None], 'FN4':[None,2,None]}
    if as_frame:
        item = pd.DataFrame(item, index=pd.date_range('2024-01-01', periods=3, name='Date'))
    before = to_json(item)
    batch = assess_financial({S:item}, [S], ['FN1','FN4']).require(True)
    assert batch.metadata['record_counts'] == {S:2}
    assert batch.metadata['dropped_records'] == {S:1}
    assert batch.metadata['missing_values'] == {S:{'FN1':2,'FN4':2}}
    assert '--' not in to_json(batch.data)
    assert to_json(item) == before
    if as_frame:
        assert '2024-01-01T00:00:00' in to_json(batch.data)


@pytest.mark.parametrize('item', [{'FN1':[1,2], 'FN4':[1]}, {'fn1':1,'FN1':2}, [1], pd.DataFrame([[1,2]], columns=['FN1','FN1'])])
def test_malformed_financial_shape_is_data_error(item):
    with pytest.raises(TdxDataError):
        assess_financial({S:item}, [S], ['FN1'])


def test_financial_no_stock_and_no_rows_are_missing():
    batch = assess_financial({S:pd.DataFrame(columns=['FN1'])}, [S,OTHER], ['FN1'])
    assert batch.metadata['missing_stocks'] == [S,OTHER]
    assert batch.metadata['record_counts'] == {S:0,OTHER:0}


def test_financial_dictionary_array_rows_are_filtered_consistently():
    batch = assess_financial({S:{'FN1':[1,None], 'Date':['20240101','20240401']}}, [S], ['FN1']).require(True)
    assert batch.data[S] == {'FN1':[1,None][:1], 'Date':['20240101']}


def test_field_list_is_nonempty_case_insensitive_and_deduplicated():
    assert parse_fields(' fn1,FN1, fn4, ') == ['FN1','FN4']
    with pytest.raises(TdxParamError):
        parse_fields(' , ')


@pytest.mark.parametrize('method,value', [
    ('query_stock_orders',[{'order_id':'fake'}]), ('query_stock_positions',[{'volume':100}]),
    ('get_financial_data',{S:{'FN1':1}}), ('get_financial_data_by_date',{S:{'FN1':1}}),
    ('order_stock','fake-order'), ('cancel_order_stock','fake-order'),
])
@pytest.mark.parametrize('code', [0,'0'])
def test_success_code_normalized_before_vendor_string_comparison(method, value, code):
    state = make_sdk(method, reply(code,value))
    result = getattr(state.sdk, method)()
    if method in ('order_stock','cancel_order_stock'):
        assert result['Value'] == value and result['ErrorId'] == '0'
    else:
        assert result == value
    assert len(state.calls) == 1 and ACTIVE_TRACE.get() is None


def test_adapter_detects_loss_of_native_nonempty_result():
    state = make_sdk('query_stock_positions', reply(0,[{'volume':100}]))
    vendor = state.module.tq.query_stock_positions
    def broken(**kwargs):
        vendor(**kwargs)
        return []
    state.module.tq.query_stock_positions = broken
    with pytest.raises(TdxDataError, match='非空'):
        state.sdk.query_stock_positions()


def test_item_errors_survive_vendor_discard_and_remain_invocation_local():
    state = make_sdk('get_financial_data', json.dumps({'ErrorId':0, 'Value':{S:{'FN1':1}},
                                                     'Errors':{OTHER:{'ErrorId':8,'Msg':'not available'}}}).encode())
    data, metadata = read_batch(state.sdk,'get_financial_data')
    assert metadata['errors'][OTHER] == {'code':'8','message':'not available'}
    batch = assess_financial(data,[S,OTHER],['FN1'],metadata).require(True)
    assert batch.metadata['errors'] == metadata['errors']
    state.response = reply(0,{S:{'FN1':2}})
    assert state.sdk.read_with_metadata('get_financial_data')[1] == {'errors':{}}
    assert ACTIVE_TRACE.get() is None
    with pytest.raises(TdxParamError):
        state.sdk.read_with_metadata('order_stock')
    assert len(state.calls) == 2


def test_errors_from_earlier_page_cannot_be_erased_by_later_success():
    state = make_sdk('get_financial_data', reply(value={}))
    def pages(**kwargs):
        state.module.dll.GetProDataInStr(1,b'{"type":1}',100)
        state.response = reply(value={S:{'FN1':1}})
        state.module.dll.GetProDataInStr(1,b'{"type":1}',100)
        return {S:{'FN1':1}}
    state.response = json.dumps({'ErrorId':'0','Value':{},'Errors':{OTHER:'page error'}}).encode()
    state.module.tq.get_financial_data = pages
    data, metadata = state.sdk.read_with_metadata('get_financial_data')
    assert data[S]['FN1'] == 1 and metadata['errors'][OTHER]['message'] == 'page error'


def test_provider_item_error_remains_partial_even_when_values_exist():
    batch = assess_financial({S:{'FN1':1}},[S],['FN1'], {'errors':{S:{'message':'field error'}}})
    with pytest.raises(TdxIncompleteDataError):
        batch.require()
    assert batch.require(True).metadata['partial'] is True


def test_legacy_nonadapter_batch_read_is_supported():
    raw = types.SimpleNamespace(get_financial_data=lambda **kwargs:{S:{'FN1':1}})
    assert read_batch(raw,'get_financial_data') == ({S:{'FN1':1}}, {})
