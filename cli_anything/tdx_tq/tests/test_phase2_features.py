"""Comprehensive unit tests for Phase 2 domain modules."""
import inspect
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import time
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from cli_anything.tdx_tq.core.client_ui import (
    publish_panel_to_tdx,
    send_client_bt,
    send_client_file,
    send_client_warn,
)
from cli_anything.tdx_tq.core.errors import TdxDataError, TdxParamError
from cli_anything.tdx_tq.core.local_cache import (
    detect_running_tdx_clients,
    lookup_local_relations,
    parse_downloaded_file,
    search_local_stocks,
)
from cli_anything.tdx_tq.core.market import (
    STOCK_LIST_CATEGORIES,
    pivot_kline_data,
    reformat_kline_data,
    stock_code,
)
from cli_anything.tdx_tq.core.watch import MAX_SUBSCRIBE_STOCKS, stream_quotes


# =====================================================================
# 1. Market tests (Categories, Vectorized Reformatting, Pivot Table)
# =====================================================================

def test_stock_list_categories_calibrated():
    """Verify calibrated categories from tdxdata_test.py:187-193."""
    assert STOCK_LIST_CATEGORIES['0'] == '自选股'
    assert STOCK_LIST_CATEGORIES['1'] == '持仓股'
    assert STOCK_LIST_CATEGORIES['5'] == '所有A股'
    assert STOCK_LIST_CATEGORIES['6'] == '上证指数成份股'
    assert STOCK_LIST_CATEGORIES['16'] == '研究行业一级'
    assert STOCK_LIST_CATEGORIES['23'] == '沪深300'
    assert STOCK_LIST_CATEGORIES['28'] == '中证A500'
    assert STOCK_LIST_CATEGORIES['31'] == 'ETF基金'
    assert STOCK_LIST_CATEGORIES['51'] == '创业板'
    assert STOCK_LIST_CATEGORIES['101'] == '国内期货'
    assert STOCK_LIST_CATEGORIES['102'] == '港股'
    assert STOCK_LIST_CATEGORIES['103'] == '美股'


def test_reformat_kline_data_vectorized():
    """Test fast vectorized extraction preserves all semantics and checks."""
    df_open = pd.DataFrame(
        {'600519.SH': [1800.0, 1820.0], '000001.SZ': [10.5, 10.8]},
        index=['20261101', '20261102'],
    )
    df_close = pd.DataFrame(
        {'600519.SH': [1815.0, 1830.0], '000001.SZ': [10.6, 11.0]},
        index=['20261101', '20261102'],
    )
    raw = {'open': df_open, 'close': df_close}
    out = reformat_kline_data(raw)

    assert set(out.keys()) == {'600519.SH', '000001.SZ'}
    assert len(out['600519.SH']) == 2
    assert out['600519.SH'][0] == {'date': '20261101', 'open': 1800.0, 'close': 1815.0}
    assert out['600519.SH'][1] == {'date': '20261102', 'open': 1820.0, 'close': 1830.0}

    # Partial overlap test (preserves missing date/stock omission)
    partial_raw = {
        'open': pd.DataFrame({'A': [1.0]}, index=['d1']),
        'close': pd.DataFrame({'A': [2.0]}, index=['d2']),
    }
    partial_out = reformat_kline_data(partial_raw)
    assert partial_out['A'] == [{'date': 'd1', 'open': 1.0}, {'date': 'd2', 'close': 2.0}]

    # Case collision raises TdxDataError
    with pytest.raises(TdxDataError, match='大小写冲突'):
        reformat_kline_data({'Close': df_close, 'close': df_close})

    # Non-numeric cell raises TdxDataError
    bad_df = pd.DataFrame({'600519.SH': ['invalid']}, index=['20261101'])
    with pytest.raises(TdxDataError, match='包含非数值数据'):
        reformat_kline_data({'close': bad_df})


def test_pivot_kline_data():
    """Test pivot_kline_data wide-table generation and ffill semantics."""
    batch_data = {
        '600519.SH': [
            {'date': '2026-11-01', 'close': 1800.0},
            {'date': '2026-11-03', 'close': 1850.0},
        ],
        '000001.SZ': [
            {'date': '2026-11-01', 'close': 10.0},
            {'date': '2026-11-02', 'close': 10.5},
            {'date': '2026-11-03', 'close': 11.0},
        ],
    }

    # None fill
    df_none = pivot_kline_data(batch_data, field='close', fill='none')
    assert list(df_none.columns) == ['600519.SH', '000001.SZ']
    assert df_none.index.name == 'date'
    assert list(df_none.index) == ['2026-11-01', '2026-11-02', '2026-11-03']
    assert pd.isna(df_none.loc['2026-11-02', '600519.SH'])
    assert df_none.loc['2026-11-03', '600519.SH'] == 1850.0

    # ffill fill
    df_ffill = pivot_kline_data(batch_data, field='close', fill='ffill')
    assert df_ffill.loc['2026-11-02', '600519.SH'] == 1800.0
    assert df_ffill.loc['2026-11-03', '600519.SH'] == 1850.0

    # Rejection of lookahead bfill or invalid fills
    with pytest.raises(TdxParamError, match='不支持的填充方式'):
        pivot_kline_data(batch_data, field='close', fill='bfill')

    # Missing field raises TdxParamError
    with pytest.raises(TdxParamError, match='不存在'):
        pivot_kline_data(batch_data, field='volume', fill='none')


# =====================================================================
# 2. Watch Engine tests (subscribe_hq streaming & debounce)
# =====================================================================

def test_watch_stream_validation_and_signature():
    """Verify watch constraints, parameter validation, and callback signature."""
    assert MAX_SUBSCRIBE_STOCKS == 100

    mock_tdx = MagicMock()

    # Reject > 100 stocks
    with pytest.raises(TdxParamError, match='超过上限'):
        list(stream_quotes(mock_tdx, [f'{i:06d}.SZ' for i in range(101)]))

    # Reject empty stocks
    with pytest.raises(TdxParamError, match='不能为空'):
        list(stream_quotes(mock_tdx, []))

    # Reject invalid debounce
    with pytest.raises(TdxParamError, match='debounce_ms'):
        list(stream_quotes(mock_tdx, ['600519.SH'], debounce_ms=-1))

    # Reject invalid duration
    with pytest.raises(TdxParamError, match='duration'):
        list(stream_quotes(mock_tdx, ['600519.SH'], duration=0))


def _tdx_is_callback_func(func):
    """Calibrated directly to tqcenter.py:278 is_callback_func."""
    if not callable(func):
        return False
    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        required_params = []
        for param in params:
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.default is inspect.Parameter.empty:
                required_params.append(param)
        return len(required_params) == 1
    except (ValueError, TypeError):
        return False


def test_watch_stream_execution_and_debounce():
    """Test streaming queue, debounce, snapshot fetching, and guaranteed unsubscribe."""
    registered_cb = None

    def fake_subscribe(stock_list, callback):
        nonlocal registered_cb
        registered_cb = callback
        assert _tdx_is_callback_func(callback) is True

    mock_tdx = MagicMock()
    mock_tdx.subscribe_hq.side_effect = fake_subscribe
    mock_tdx.get_market_snapshot.return_value = {'Now': 1800.0, 'Code': '600519.SH'}

    # Controlled monotonic clock
    simulated_time = 1000.0

    def fake_clock():
        return simulated_time

    streamer = stream_quotes(
        mock_tdx,
        ['600519.SH'],
        with_snapshot=True,
        debounce_ms=500,
        max_events=2,
        poll_interval=0.01,
        clock=fake_clock,
    )

    # Prime the generator to subscribe
    # We call next() in a separate push pattern:
    # First, let subscriber register
    import threading
    events = []

    def run_consumer():
        for event in streamer:
            events.append(event)

    t = threading.Thread(target=run_consumer)
    t.start()

    time.sleep(0.05)
    assert registered_cb is not None
    assert _tdx_is_callback_func(registered_cb) is True

    # 1. Send first event at t=1000.0
    registered_cb(json.dumps({'Code': '600519.SH', 'ErrorId': '0'}))
    time.sleep(0.05)

    # 2. Send second event at t=1000.1 (< 500ms -> should be debounced!)
    simulated_time = 1000.1
    registered_cb(json.dumps({'Code': '600519.SH', 'ErrorId': '0'}))
    time.sleep(0.05)

    # 3. Send third event at t=1001.0 (> 500ms -> should be emitted as event 2!)
    simulated_time = 1001.0
    registered_cb(json.dumps({'Code': '600519.SH', 'ErrorId': '0'}))

    t.join(timeout=2.0)

    assert len(events) == 2
    assert events[0]['seq'] == 1
    assert events[0]['stock'] == '600519.SH'
    assert events[0]['snapshot'] == {'Now': 1800.0, 'Code': '600519.SH'}
    assert events[1]['seq'] == 2

    # Guaranteed unsubscribe called
    mock_tdx.unsubscribe_hq.assert_called_once_with(stock_list=['600519.SH'])


# =====================================================================
# 3. Client UI Linkage tests (print_to_tdx, send_file, send_bt, send_warn)
# =====================================================================

def test_publish_panel_cwd_isolation(tmp_path):
    """Test print_to_tdx cwd isolation and parameter validation."""
    orig_cwd = os.getcwd()

    df1 = pd.DataFrame({'date': ['20261101', '20261102'], 'ret': [0.01, 0.02]})
    df2 = pd.DataFrame({'date': ['20261101', '20261102'], 'vol': [100.0, 200.0]})

    tdx_root = tmp_path / "tdx_install"
    tdx_root.mkdir()

    mock_tdx = MagicMock()
    called_cwd = []

    def mock_print_to_tdx(**kwargs):
        called_cwd.append(os.getcwd())
        # create mock files as tqcenter would
        xml_name = kwargs['xml_filename']
        with open(xml_name, 'w') as f:
            f.write('<xml/>')
        for j in kwargs['jsn_filenames']:
            with open(j, 'w') as f:
                f.write('[]')

    mock_tdx.print_to_tdx = mock_print_to_tdx

    res = publish_panel_to_tdx(
        mock_tdx,
        str(tdx_root),
        [df1, df2],
        sp_name='test_panel',
        layout='horizontal',
        titles=['收益', '波动'],
    )

    # Verify cwd was isolated and restored
    assert os.getcwd() == orig_cwd
    assert len(called_cwd) == 1
    assert called_cwd[0] != orig_cwd  # Executed inside temp directory!

    assert res['group_count'] == 2
    assert res['layout'] == 'horizontal'
    assert res['titles'] == ['收益', '波动']
    assert Path(res['sp_path']).name == 'test_panel.sp'

    # Unsafe sp_name rejection
    with pytest.raises(TdxParamError, match='sp_name'):
        publish_panel_to_tdx(mock_tdx, str(tdx_root), [df1], sp_name='../bad/stem')

    # Single column DataFrame rejection (< 2 columns)
    with pytest.raises(TdxParamError, match='列数至少为 2'):
        publish_panel_to_tdx(mock_tdx, str(tdx_root), [pd.DataFrame({'only_date': ['20261101']})], sp_name='test')


def test_send_client_file(tmp_path):
    """Test send_client_file staging and path routing."""
    tdx_root = tmp_path / "TDX"
    test_file = tmp_path / "report.pdf"
    test_file.write_text("dummy")

    mock_tdx = MagicMock()
    mock_tdx.send_file.return_value = {'ErrorId': '0'}

    # 1. Staging mode copies file to PYPlugins/file and passes relative name
    res = send_client_file(mock_tdx, str(tdx_root), str(test_file), stage_to_plugins=True)
    staged_dest = tdx_root / "PYPlugins" / "file" / "report.pdf"
    assert staged_dest.is_file()
    assert res['arg_passed'] == 'report.pdf'
    mock_tdx.send_file.assert_called_with(file_path='report.pdf')

    # 2. Non-staged mode passes absolute path
    res2 = send_client_file(mock_tdx, str(tdx_root), str(test_file), stage_to_plugins=False)
    assert res2['arg_passed'] == str(test_file.resolve())

    # 3. Missing file raises TdxParamError
    with pytest.raises(TdxParamError, match='不存在'):
        send_client_file(mock_tdx, str(tdx_root), str(tmp_path / "nonexistent.txt"))


def test_send_client_bt():
    """Test send_client_bt 16-element limit and numeric conversion."""
    mock_tdx = MagicMock()
    mock_tdx.send_bt_data.return_value = {'ErrorId': '0'}

    # Valid call
    times = ['20261101140000', '20261101140500']
    series = [
        [1, 0, 100.5],
        ['0', 1, 200],
    ]
    res = send_client_bt(mock_tdx, '600519.SH', times, series)
    assert res['count'] == 2
    assert res['series'] == [['1', '0', '100.5'], ['0', '1', '200']]
    mock_tdx.send_bt_data.assert_called_once()

    # Reject inner list > 16 elements
    too_long_series = [[str(i) for i in range(17)]]
    with pytest.raises(TdxParamError, match='1 到 16 个数值'):
        send_client_bt(mock_tdx, '600519.SH', ['20261101140000'], too_long_series)

    # Reject non-numeric element
    bad_series = [['1', 'bad_number']]
    with pytest.raises(TdxParamError, match='无效的数值'):
        send_client_bt(mock_tdx, '600519.SH', ['20261101140000'], bad_series)


def test_send_client_warn():
    """Test send_client_warn BS mapping and 50 GBK bytes reason truncation."""
    mock_tdx = MagicMock()
    mock_tdx.send_warn.return_value = {'ErrorId': '0'}

    stocks = ['600519.SH', '000001.SZ', '688318.SH']
    reasons = [
        '正常预警',
        '这是一个超长原因描述这是一段非常长超过五十个字节的中文文本需要被安全截断不会导致乱码',
        '简短原因',
    ]
    prices = [1800.5, '10.50', 50]
    bs_flags = ['buy', 'S', 'unknown']

    res = send_client_warn(
        mock_tdx,
        stocks,
        reasons,
        prices,
        bs_flags=bs_flags,
    )

    assert res['count'] == 3
    assert res['bs_flags'] == ['0', '1', '2']
    assert res['truncated_reasons'] is True
    # Verify second reason is <= 50 GBK bytes
    assert len(res['reasons'][1].encode('gbk', errors='ignore')) <= 50

    # Reject invalid BS flag
    with pytest.raises(TdxParamError, match='无效的买卖标志'):
        send_client_warn(mock_tdx, ['600519.SH'], ['test'], [100], bs_flags=['invalid_flag'])


# =====================================================================
# 4. Local TDX Cache & Download File Parsers
# =====================================================================

def _create_synthetic_tnf(fpath: Path, records: list[tuple[str, str, str]]):
    """Write synthetic TDX .tnf binary file (50-byte header, 360-byte records)."""
    with open(fpath, 'wb') as f:
        # Header: 50 bytes
        f.write(b'\x00' * 50)
        for code, name, pinyin in records:
            rec = bytearray(360)
            # 0..6 code ASCII
            code_bytes = code.encode('ascii')[:6]
            rec[0:len(code_bytes)] = code_bytes
            # 31..77 name GBK
            name_bytes = name.encode('gbk')[:46]
            rec[31:31 + len(name_bytes)] = name_bytes
            # 329..338 pinyin ASCII
            py_bytes = pinyin.encode('ascii')[:9]
            rec[329:329 + len(py_bytes)] = py_bytes
            f.write(rec)


def test_search_local_stocks_synthetic(tmp_path):
    """Test search_local_stocks against synthetic .tnf binary files."""
    hq_cache = tmp_path / "T0002" / "hq_cache"
    hq_cache.mkdir(parents=True)

    records = [
        ('600519', '贵州茅台', 'GZMT'),
        ('601398', '工商银行', 'GSYH'),
    ]
    _create_synthetic_tnf(hq_cache / 'shs.tnf', records)

    # Search by code
    res_code = search_local_stocks(str(tmp_path), '600519')
    assert len(res_code) == 1
    assert res_code[0]['Code'] == '600519.SH'
    assert res_code[0]['Name'] == '贵州茅台'

    # Search by pinyin
    res_py = search_local_stocks(str(tmp_path), 'gzmt')
    assert len(res_py) == 1
    assert res_py[0]['Code'] == '600519.SH'

    # Search by name
    res_name = search_local_stocks(str(tmp_path), '工行' if False else '银行')
    assert len(res_name) == 1
    assert res_name[0]['Code'] == '601398.SH'

    # Missing cache dir raises TdxDataError
    with pytest.raises(TdxDataError, match='未找到通达信本地行情缓存目录'):
        search_local_stocks(str(tmp_path / "empty_dir"), '600519')


def test_lookup_local_relations_synthetic(tmp_path):
    """Test lookup_local_relations with synthetic tdxzs3, tdxhy, and infoharbor."""
    hq_cache = tmp_path / "T0002" / "hq_cache"
    hq_cache.mkdir(parents=True)

    # tdxzs3.cfg: name|88xxxx|type|...|key
    zs3_content = (
        "白酒|880381|2|1|0|T030501\n"
        "食品饮料|881129|12|1|0|X21\n"
    )
    (hq_cache / "tdxzs3.cfg").write_bytes(zs3_content.encode('gbk'))

    # tdxhy.cfg: market|code6|tdx_hy|...|research_hy
    tdxhy_content = "1|600519|T030501|||X21\n"
    (hq_cache / "tdxhy.cfg").write_bytes(tdxhy_content.encode('gbk'))

    # infoharbor_block.dat: #GN_<name>,<count>,<code88>... followed by members
    infoharbor_content = (
        "#GN_通达信88,88,880515,20050607,20260512,,\n"
        "0#000001,1#600519,0#000002\n"
        "#FG_高分红股,350,880526,20240328,20260430,,\n"
        "1#600519,1#601398\n"
    )
    (hq_cache / "infoharbor_block.dat").write_bytes(infoharbor_content.encode('gbk'))

    relations = lookup_local_relations(str(tmp_path), '600519.SH')
    types = {r['BlockType']: r for r in relations}

    assert 'industry' in types
    assert types['industry']['BlockName'] == '白酒'
    assert types['industry']['BlockCode'] == '880381.SH'

    assert 'research_industry' in types
    assert types['research_industry']['BlockName'] == '食品饮料'

    assert 'concept' in types
    assert types['concept']['BlockName'] == '通达信88'
    assert types['concept']['Count'] == 88

    assert 'style' in types
    assert types['style']['BlockName'] == '高分红股'
    assert types['style']['Count'] == 350


def test_parse_downloaded_file_synthetic(tmp_path):
    """Test parse_downloaded_file with synthetic top10 shareholders json."""
    plugins_data = tmp_path / "PYPlugins" / "data"
    plugins_data.mkdir(parents=True)

    sample_gdxx = {
        "20251231": {
            "gd": [
                {"pm": "1", "name": "中国贵州茅台酒厂(集团)有限责任公司", "cgsl": "681282935", "cgbl": "54.4000"},
                {"pm": "2", "name": "贵州省国有资本运营有限责任公司", "cgsl": "56996777", "cgbl": "4.5500"},
            ],
            "ltgd": [
                {"pm": "1", "name": "中国贵州茅台酒厂(集团)有限责任公司", "cgsl": "681282935", "cgbl": "54.4038"},
            ],
        }
    }
    outer = [{"gdxx": json.dumps(sample_gdxx)}]
    target_file = plugins_data / "gp600519_2025.json"
    target_file.write_text(json.dumps(outer), encoding='utf-8')

    res = parse_downloaded_file(str(tmp_path), '600519.SH', down_type=1)
    assert res['stock'] == '600519.SH'
    assert res['down_type'] == 1
    assert '20251231' in res['reports']

    report = res['reports']['20251231']
    assert len(report['top10_shareholders']) == 2
    # Verify pm converted to int, cgsl and cgbl converted to float
    top1 = report['top10_shareholders'][0]
    assert top1['pm'] == 1
    assert top1['name'] == '中国贵州茅台酒厂(集团)有限责任公司'
    assert top1['cgsl'] == 681282935.0
    assert top1['cgbl'] == 54.4


def test_detect_running_tdx_clients():
    """Verify detect_running_tdx_clients never raises and returns list."""
    clients = detect_running_tdx_clients(timeout=2.0)
    assert isinstance(clients, list)
    for c in clients:
        assert isinstance(c, str)
        assert Path(c).is_dir()


@pytest.mark.skipif(not Path(r"D:\TDX64").is_dir(), reason="Real TDX64 install not available")
def test_real_tdx64_cache_and_parsers():
    """Integration test against actual TDX64 directory on host."""
    p = r"D:\TDX64"
    res = search_local_stocks(p, 'GZMT')
    assert any(s['Code'] == '600519.SH' and s['Name'] == '贵州茅台' for s in res)

    rel = lookup_local_relations(p, '600519.SH')
    assert len(rel) > 0
    assert any(b['BlockName'] == '白酒' for b in rel)

    dl = parse_downloaded_file(p, '600519.SH', 1)
    assert dl['stock'] == '600519.SH'
    assert len(dl['reports']) > 0


def test_watch_edge_payloads_and_duration_expiry():
    """Cover bytes/GBK/dict/non-JSON callback payloads, no-snapshot mode, and duration expiry."""
    registered_cb = None

    def fake_subscribe(stock_list, callback):
        nonlocal registered_cb
        registered_cb = callback

    mock_tdx = MagicMock()
    mock_tdx.subscribe_hq.side_effect = fake_subscribe

    import threading
    events = []
    streamer = stream_quotes(
        mock_tdx,
        ['600519.SH'],
        with_snapshot=False,
        debounce_ms=0,
        duration=1.5,
        max_events=4,
        poll_interval=0.01,
    )

    def consume():
        for ev in streamer:
            events.append(ev)

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.03)
    assert registered_cb is not None
    # bytes UTF-8, bytes GBK, dict without Code (single-stock fallback), and raw non-JSON string
    registered_cb(b'{"Code": "600519.SH", "ErrorId": "0"}')
    registered_cb('{"code": "600519.SH"}'.encode('gbk'))
    registered_cb({'ErrorId': '0'})
    registered_cb('raw-non-json')
    t.join(timeout=2.0)

    assert len(events) >= 3
    assert all(ev['snapshot'] is None for ev in events)
    mock_tdx.unsubscribe_hq.assert_called_once()


def test_local_cache_ini_and_etf_download_edges(tmp_path):
    """Cover code2name_hk.ini search, limit cap, and down_type=2 ETF PCF parsing."""
    hq_cache = tmp_path / "T0002" / "hq_cache"
    hq_cache.mkdir(parents=True)
    (hq_cache / "code2name_hk.ini").write_bytes("[HK]\n00700,腾讯控股\n09988,阿里巴巴\n".encode('gbk'))

    hk = search_local_stocks(str(tmp_path), '腾讯', limit=1)
    assert hk == [{'Code': '00700.HK', 'Name': '腾讯控股', 'Pinyin': '', 'source': 'local_tdx_cache'}]

    with pytest.raises(TdxParamError):
        search_local_stocks(str(tmp_path), '   ')
    with pytest.raises(TdxParamError):
        search_local_stocks(str(tmp_path), '腾讯', limit=0)

    plugins_data = tmp_path / "PYPlugins" / "data"
    plugins_data.mkdir(parents=True)
    (plugins_data / "etf_510300_20261023.txt").write_bytes("FundCode=510300\nIOPV=4.12\n".encode('gbk'))
    (plugins_data / "etf_159915_20261023.json").write_text(json.dumps({"iopv": 2.15}), encoding='utf-8')

    etf_txt = parse_downloaded_file(str(tmp_path), '510300.SH', down_type=2, down_date='20261023')
    assert etf_txt['down_type'] == 2 and 'FundCode=510300' in etf_txt['content']

    etf_json = parse_downloaded_file(str(tmp_path), '159915.SZ', down_type=2, down_date='20261023')
    assert etf_json['content'] == {'iopv': 2.15}

    with pytest.raises(TdxParamError):
        parse_downloaded_file(str(tmp_path), '510300.SH', down_type=9)


def test_client_ui_and_session_lifecycle_edges(tmp_path, monkeypatch):
    """Cover client_ui parameter guards and Session initialize/cleanup state transitions."""
    df = pd.DataFrame({'date': ['20261101'], 'v': [1.0]})
    mock_tdx = MagicMock()

    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [], sp_name='ok')
    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [df], sp_name='ok', layout='diagonal')
    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [df], sp_name='ok', xml_filename='bad.txt')
    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [df], sp_name='ok', jsn_filenames=['a.jsn', 'b.jsn'])
    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [df], sp_name='ok', jsn_filenames=['../bad.jsn'])
    with pytest.raises(TdxParamError):
        publish_panel_to_tdx(mock_tdx, str(tmp_path), [df], sp_name='ok', heights=['0.5', '0.5'])
    with pytest.raises(TdxParamError):
        send_client_warn(mock_tdx, ['600519.SH'], ['ok'], [True])

    from cli_anything.tdx_tq.core.session import Session
    from cli_anything.tdx_tq.core.errors import TdxInitError
    s = Session()
    released = []

    class HalfInitRaw:
        _initialized = True
        def _release(self):
            released.append(True)

    class FailingAdapter:
        raw = HalfInitRaw()
        run_id = -1
        def initialize(self, identity):
            raise RuntimeError("handshake failed")

    monkeypatch.setattr(s, '_tdx', FailingAdapter())
    monkeypatch.setattr(s, '_loaded_path', str(Path(r'D:\tdx').resolve()))
    monkeypatch.setattr(s, '_initialized', False)
    with pytest.raises(TdxInitError, match="handshake failed"):
        s.initialize("unit_test")
    assert s.state == 'failed'
    assert released == [True]


