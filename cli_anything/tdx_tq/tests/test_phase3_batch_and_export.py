"""Comprehensive tests for Phase 3 features:
- .jsonl / .parquet atomic exports and format resolution
- High-speed vectorized K-line reformatting with strict data contracts
- Chunked batch market data fetching with progress reporting and fault tolerance
- Batch snapshot fetching with strict vs partial modes
"""
import datetime
import math
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from cli_anything.tdx_tq.core.errors import (
    TdxDataError,
    TdxIncompleteDataError,
    TdxParamError,
    TdxReadTimeoutError,
)
from cli_anything.tdx_tq.core.export import (
    EXTENSIONS,
    export_dataframe,
    export_dict,
    resolve_export_format,
)
from cli_anything.tdx_tq.core.market import (
    MarketRequest,
    fetch_market_data_chunked,
    fetch_snapshots_batch,
    reformat_kline_data,
)
from cli_anything.tdx_tq.core.quality import assess_market


# =====================================================================
# 1. Export Format & Atomic Export Tests (.jsonl, .ndjson, .parquet)
# =====================================================================

def test_resolve_export_format():
    assert resolve_export_format('data.csv') == 'csv'
    assert resolve_export_format('/path/to/file.JSON') == 'json'
    assert resolve_export_format('output.jsonl') == 'jsonl'
    assert resolve_export_format('output.ndjson') == 'jsonl'
    assert resolve_export_format('report.XLSX') == 'excel'
    assert resolve_export_format('cache.parquet') == 'parquet'

    with pytest.raises(TdxParamError, match="输出文件扩展名必须为 .csv、.json、.jsonl、.xlsx 或 .parquet"):
        resolve_export_format('data.txt')

    with pytest.raises(TdxParamError, match="输出文件扩展名必须为 .csv、.json、.jsonl、.xlsx 或 .parquet"):
        resolve_export_format('data.h5')

    with pytest.raises(TdxParamError, match="输出文件扩展名必须为 .csv、.json、.jsonl、.xlsx 或 .parquet"):
        resolve_export_format('data')


def test_export_jsonl_and_ndjson(tmp_path):
    df = pd.DataFrame({
        'code': ['600519.SH', '000001.SZ'],
        'price': [1800.5, 10.2],
        'volume': [1000, 2500],
        'date': [datetime.date(2026, 11, 1), datetime.date(2026, 11, 2)],
    })

    # Test .jsonl
    out_jsonl = tmp_path / 'out.jsonl'
    res = export_dataframe(df, str(out_jsonl), format='jsonl')
    assert Path(res).is_file()

    lines = out_jsonl.read_text(encoding='utf-8').strip().split('\n')
    assert len(lines) == 2
    import json
    row0 = json.loads(lines[0])
    assert row0['code'] == '600519.SH'
    assert row0['price'] == 1800.5
    assert row0['date'] == '2026-11-01'

    # Test .ndjson
    out_ndjson = tmp_path / 'out.ndjson'
    res2 = export_dataframe(df, str(out_ndjson), format='jsonl')
    assert Path(res2).is_file()
    lines2 = out_ndjson.read_text(encoding='utf-8').strip().split('\n')
    assert len(lines2) == 2

    # Mismatched extension
    with pytest.raises(ValueError, match="jsonl 导出需要 .jsonl 扩展名"):
        export_dataframe(df, str(tmp_path / 'out.json'), format='jsonl')


def test_export_parquet_missing_pyarrow_raises_tdx_param_error(tmp_path, monkeypatch):
    df = pd.DataFrame({'a': [1, 2, 3]})
    out_parquet = tmp_path / 'out.parquet'

    def fail_to_parquet(*args, **kwargs):
        raise ImportError("pyarrow is required for parquet support")

    monkeypatch.setattr(pd.DataFrame, 'to_parquet', fail_to_parquet)
    with pytest.raises(TdxParamError, match="导出 .parquet 需要安装 pyarrow"):
        export_dataframe(df, str(out_parquet), format='parquet')


def test_export_parquet_mocked_success(tmp_path, monkeypatch):
    df = pd.DataFrame({'a': [1, 2, 3]})
    out_parquet = tmp_path / 'out.parquet'

    def fake_to_parquet(self, path, **kwargs):
        Path(path).write_bytes(b'PAR1mock_parquet_data')

    monkeypatch.setattr(pd.DataFrame, 'to_parquet', fake_to_parquet)
    res = export_dataframe(df, str(out_parquet), format='parquet')
    assert Path(res).is_file()
    assert out_parquet.read_bytes() == b'PAR1mock_parquet_data'

    # Mismatched extension
    with pytest.raises(ValueError, match="parquet 导出需要 .parquet 扩展名"):
        export_dataframe(df, str(tmp_path / 'out.csv'), format='parquet')


def test_export_dict_all_formats(tmp_path, monkeypatch):
    data = {'s1': pd.DataFrame({'close': [10.0, 11.0]})}

    # csv
    res_csv = export_dict(data, str(tmp_path / 'csv_out'), format='csv')
    assert 's1' in res_csv and Path(res_csv['s1']).exists()

    # jsonl
    res_jsonl = export_dict(data, str(tmp_path / 'jsonl_out'), format='jsonl')
    assert 's1' in res_jsonl and res_jsonl['s1'].endswith('.jsonl') and Path(res_jsonl['s1']).exists()

    # parquet (mocked)
    def fake_to_parquet(self, path, **kwargs):
        Path(path).write_bytes(b'PAR1mock')

    monkeypatch.setattr(pd.DataFrame, 'to_parquet', fake_to_parquet)
    res_parquet = export_dict(data, str(tmp_path / 'parquet_out'), format='parquet')
    assert 's1' in res_parquet and res_parquet['s1'].endswith('.parquet') and Path(res_parquet['s1']).exists()


# =====================================================================
# 2. Vectorized K-Line Fast-Path & Performance Benchmark
# =====================================================================

def test_reformat_kline_numeric_fast_path():
    df_close = pd.DataFrame({
        '600519.SH': [1800.0, np.nan, np.inf, 1850.5],
        '000001.SZ': [10.0, 10.5, 11.0, -np.inf],
    }, index=['20261101', '20261102', '20261103', '20261104'])

    res = reformat_kline_data({'close': df_close})
    bars = res['600519.SH']
    assert len(bars) == 4
    assert bars[0] == {'date': '20261101', 'close': 1800.0}
    assert bars[1] == {'date': '20261102', 'close': None}
    assert bars[2] == {'date': '20261103', 'close': None}
    assert bars[3] == {'date': '20261104', 'close': 1850.5}

    sz_bars = res['000001.SZ']
    assert sz_bars[3] == {'date': '20261104', 'close': None}


def test_reformat_kline_bool_dtype_rejected():
    df_bool = pd.DataFrame({'600519.SH': [True, False]}, index=['20261101', '20261102'])
    with pytest.raises(TdxDataError, match="K 线字段 close 包含非数值数据"):
        reformat_kline_data({'close': df_bool})


def test_reformat_kline_object_dtype_handling():
    # Object column with bool rejected
    df_obj_bool = pd.DataFrame({'600519.SH': [1800.0, True]}, index=['20261101', '20261102'], dtype=object)
    with pytest.raises(TdxDataError, match="K 线字段 close 包含非数值数据"):
        reformat_kline_data({'close': df_obj_bool})

    # Object column with invalid string rejected
    df_obj_str = pd.DataFrame({'600519.SH': ['invalid', 1800.0]}, index=['20261101', '20261102'], dtype=object)
    with pytest.raises(TdxDataError, match="K 线字段 close 包含非数值数据"):
        reformat_kline_data({'close': df_obj_str})

    # Object column with numeric string and None/nan
    df_obj_valid = pd.DataFrame(
        {'600519.SH': ['1800.5', None, 'nan']},
        index=['20261101', '20261102', '20261103'],
        dtype=object,
    )
    res = reformat_kline_data({'close': df_obj_valid})
    assert res['600519.SH'][0]['close'] == 1800.5
    assert res['600519.SH'][1]['close'] is None
    assert res['600519.SH'][2]['close'] is None


def test_reformat_kline_benchmark_performance():
    """Verify 20 stocks x 1,000 bars x 5 fields completes in < 0.5s."""
    n_stocks = 20
    n_bars = 1000
    stocks = [f'{600000 + i:06d}.SH' for i in range(n_stocks)]
    dates = [f'20220101{i:04d}' for i in range(n_bars)]
    fields = ['open', 'high', 'low', 'close', 'volume']

    raw_data = {}
    for f in fields:
        matrix = np.random.uniform(10.0, 100.0, size=(n_bars, n_stocks))
        # Add some NaNs to exercise both branches
        matrix[matrix > 90.0] = np.nan
        raw_data[f] = pd.DataFrame(matrix, index=dates, columns=stocks)

    start = time.perf_counter()
    out = reformat_kline_data(raw_data)
    elapsed = time.perf_counter() - start

    assert len(out) == n_stocks
    assert len(out[stocks[0]]) == n_bars
    assert 'close' in out[stocks[0]][0]
    assert elapsed < 0.5, f"Vectorized reformat took {elapsed:.3f}s (must be < 0.5s)"


# =====================================================================
# 3. Chunked Batch Market Data Fetching
# =====================================================================

def test_fetch_market_data_chunked_invalid_chunk_size():
    req = MarketRequest(period='1d', count=10)
    for bad_size in (0, -1, '5', True, False):
        with pytest.raises(TdxParamError, match="chunk_size 必须为正整数"):
            fetch_market_data_chunked(MagicMock(), ['600519.SH'], request=req, chunk_size=bad_size)


def test_fetch_market_data_chunked_single_chunk():
    mock_tdx = MagicMock()
    req = MarketRequest(period='1d', count=2)
    stocks = ['600519.SH', '000001.SZ']

    df_close = pd.DataFrame({
        '600519.SH': [1800.0, 1810.0],
        '000001.SZ': [10.0, 10.5],
    }, index=['20261101', '20261102'])
    mock_tdx.get_market_data.return_value = {'close': df_close}

    progress_calls = []

    def on_prog(*args):
        progress_calls.append(args)

    data, meta = fetch_market_data_chunked(
        mock_tdx, stocks, request=req, chunk_size=10, on_progress=on_prog
    )

    assert set(data.keys()) == set(stocks)
    assert len(progress_calls) == 1
    assert progress_calls[0] == (1, 1, 2, 2)
    mock_tdx.get_market_data.assert_called_once()


def test_fetch_market_data_chunked_multi_chunk_with_empty_chunk_surviving():
    """Verify multi-chunk fault tolerance where 1 halted chunk fails with empty error,

    and assess_market(...).require(allow_partial=True) succeeds.
    """
    mock_tdx = MagicMock()
    req = MarketRequest(period='1d', count=2)
    stocks = ['600519.SH', '600036.SH', '000001.SZ', '000002.SZ']
    # chunk_size = 2 -> chunk 1: 600519, 600036; chunk 2: 000001, 000002

    df_chunk1 = pd.DataFrame({
        '600519.SH': [1800.0, 1810.0],
        '600036.SH': [35.0, 35.5],
    }, index=['20261101', '20261102'])

    def fake_get_market_data(stock_list, **kwargs):
        if '600519.SH' in stock_list:
            return {'close': df_chunk1}
        # Halted/empty chunk raises standard empty TdxDataError
        raise TdxDataError("get_market_data 没有返回可用数据，请核对证券、缓存、日期和权限")

    mock_tdx.get_market_data.side_effect = fake_get_market_data

    progress_calls = []

    def on_prog(*args):
        progress_calls.append(args)

    data, meta = fetch_market_data_chunked(
        mock_tdx, stocks, request=req, chunk_size=2, on_progress=on_prog
    )

    # Chunk 1 data is retained
    assert set(data.keys()) == {'600519.SH', '600036.SH'}
    assert len(progress_calls) == 2
    assert progress_calls[0] == (1, 2, 2, 4)
    assert progress_calls[1] == (2, 2, 4, 4)

    # Evaluate under assess_market completeness contract
    assessed = assess_market(data, stocks, metadata=meta)
    with pytest.raises(TdxIncompleteDataError):
        assessed.require(allow_partial=False)

    partial_result = assessed.require(allow_partial=True)
    assert partial_result.metadata['partial'] is True
    assert partial_result.metadata['missing_stocks'] == ['000001.SZ', '000002.SZ']
    assert set(partial_result.metadata['available_stocks']) == {'600519.SH', '600036.SH'}


def test_fetch_market_data_chunked_all_empty_raises():
    mock_tdx = MagicMock()
    req = MarketRequest(period='1d', count=2)
    mock_tdx.get_market_data.side_effect = TdxDataError("get_market_data 没有返回可用数据，请核对证券、缓存、日期和权限")

    with pytest.raises(TdxDataError, match="没有返回可用数据"):
        fetch_market_data_chunked(mock_tdx, ['600519.SH', '000001.SZ'], request=req, chunk_size=1)


def test_fetch_market_data_chunked_propagates_timeout_and_corruption():
    mock_tdx = MagicMock()
    req = MarketRequest(period='1d', count=2)

    # 1. Timeout error should not be swallowed
    mock_tdx.get_market_data.side_effect = TdxReadTimeoutError("get_market_data 请求超时")
    with pytest.raises(TdxReadTimeoutError, match="请求超时"):
        fetch_market_data_chunked(mock_tdx, ['600519.SH', '000001.SZ'], request=req, chunk_size=1)

    # 2. Date interval violation should propagate immediately
    bad_dates_df = pd.DataFrame({'600519.SH': [1800.0]}, index=['20200101'])
    req_interval = MarketRequest(period='1d', start='20261101', end='20261102')
    mock_tdx.get_market_data.side_effect = None
    mock_tdx.get_market_data.return_value = {'close': bad_dates_df}

    with pytest.raises(TdxDataError, match="超出请求区间的时间"):
        fetch_market_data_chunked(mock_tdx, ['600519.SH', '000001.SZ'], request=req_interval, chunk_size=1)


# =====================================================================
# 4. Batch Snapshot Fetching (Strict vs Partial)
# =====================================================================

def test_fetch_snapshots_batch_strict_success():
    mock_tdx = MagicMock()
    stocks = ['600519.SH', '000001.SZ']
    mock_tdx.get_market_snapshot.side_effect = lambda s, **kw: {'Code': s, 'Now': 100.0}

    prog = []
    res = fetch_snapshots_batch(mock_tdx, stocks, allow_partial=False, on_progress=lambda *a: prog.append(a))

    assert isinstance(res, dict)
    assert set(res.keys()) == set(stocks)
    assert res['600519.SH']['Now'] == 100.0
    assert len(prog) == 2


def test_fetch_snapshots_batch_strict_failure_raises_immediately():
    mock_tdx = MagicMock()
    stocks = ['600519.SH', '000001.SZ']

    def fake_snapshot(s, **kw):
        if s == '600519.SH':
            return {'Code': s, 'Now': 100.0}
        raise TdxDataError("get_market_snapshot 没有返回可用数据，请核对证券、缓存、日期和权限")

    mock_tdx.get_market_snapshot.side_effect = fake_snapshot

    with pytest.raises(TdxDataError, match="没有返回可用数据"):
        fetch_snapshots_batch(mock_tdx, stocks, allow_partial=False)


def test_fetch_snapshots_batch_partial_success():
    mock_tdx = MagicMock()
    stocks = ['600519.SH', '000001.SZ', '000002.SZ']

    def fake_snapshot(s, **kw):
        if s == '000001.SZ':
            raise TdxDataError("证券未找到或无快照")
        return {'Code': s, 'Now': 50.0}

    mock_tdx.get_market_snapshot.side_effect = fake_snapshot

    res = fetch_snapshots_batch(mock_tdx, stocks, allow_partial=True)

    assert res['schema_version'] == 1
    assert res['success'] is True
    assert res['partial'] is True
    assert res['requested_stocks'] == stocks
    assert res['available_stocks'] == ['600519.SH', '000002.SZ']
    assert res['missing_stocks'] == ['000001.SZ']
    assert '000001.SZ' in res['errors']
    assert res['errors']['000001.SZ']['code'] == 'SDK_DATA_ERROR'
    assert '部分证券实时快照不可用' in res['warnings']
    assert set(res['data'].keys()) == {'600519.SH', '000002.SZ'}


def test_fetch_snapshots_batch_all_failed_raises_tdx_incomplete():
    mock_tdx = MagicMock()
    stocks = ['600519.SH', '000001.SZ']
    mock_tdx.get_market_snapshot.side_effect = TdxDataError("快照失败")

    with pytest.raises(TdxIncompleteDataError) as exc_info:
        fetch_snapshots_batch(mock_tdx, stocks, allow_partial=True)

    err = exc_info.value
    assert err.code == "DATA_INCOMPLETE"
    assert err.details['partial'] is True
    assert err.details['missing_stocks'] == stocks
    assert err.data == {}
