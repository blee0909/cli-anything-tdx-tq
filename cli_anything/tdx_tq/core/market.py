"""Market requests and lossless normalization, independent of Click."""
import datetime
import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .errors import TdxDataError, TdxIncompleteDataError, TdxParamError, TdxReadTimeoutError
from .sdk import validate_result

SUPPORTED_PERIODS = ('1m', '5m', '10m', '15m', '30m', '1h', '1d', '1w', '1mon', '45d', '1q', '1y')

STOCK_LIST_CATEGORIES: dict[str, str] = {
    '0': '自选股',
    '1': '持仓股',
    '5': '所有A股',
    '6': '上证指数成份股',
    '7': '上证主板',
    '8': '深证主板',
    '9': '重点指数',
    '10': '所有板块指数',
    '11': '缺省行业板块',
    '12': '概念板块',
    '13': '风格板块',
    '14': '地区板块',
    '15': '缺省行业分类+概念板块',
    '16': '研究行业一级',
    '17': '研究行业二级',
    '18': '研究行业三级',
    '21': '含H股',
    '22': '含可转债',
    '23': '沪深300',
    '24': '中证500',
    '25': '中证1000',
    '26': '国证2000',
    '27': '中证2000',
    '28': '中证A500',
    '30': 'REITs',
    '31': 'ETF基金',
    '32': '可转债',
    '33': 'LOF基金',
    '34': '所有可交易基金',
    '35': '所有沪深基金',
    '36': 'T+0基金',
    '49': '金融类企业',
    '50': '沪深A股',
    '51': '创业板',
    '52': '科创板',
    '53': '北交所',
    '101': '国内期货',
    '102': '港股',
    '103': '美股',
}


def normalize_period(value: str) -> str:
    value = {'60m': '1h'}.get(value.lower(), value.lower())
    if value not in SUPPORTED_PERIODS:
        raise TdxParamError(f"不支持的周期 {value}; 可用周期: {', '.join(SUPPORTED_PERIODS)} (60m=1h)")
    return value


def stock_code(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z][A-Za-z0-9]*', value):
        raise TdxParamError(f"证券代码格式无效: {value!r}，例如 600519.SH")
    code, suffix = value.rsplit('.', 1)
    suffix = suffix.upper()
    if suffix in ('SH', 'SZ', 'BJ') and (not code.isdigit() or len(code) != 6):
        raise TdxParamError('沪深北证券代码必须为六位数字')
    return f'{code}.{suffix}'


def date_bound(value: str | None, *, end=False) -> datetime.datetime | None:
    if value is None:
        return None
    if not re.fullmatch(r'[0-9]{8}|[0-9]{14}', value):
        raise TdxParamError(f"无效日期: {value}，请使用 YYYYMMDD 或 YYYYMMDDHHMMSS")
    try:
        result = datetime.datetime.strptime(value, '%Y%m%d' if len(value) == 8 else '%Y%m%d%H%M%S')
    except ValueError as exc:
        raise TdxParamError(f"无效日期: {value}") from exc
    return result.replace(hour=23, minute=59, second=59) if end and len(value) == 8 else result


def validate_dates(start: str | None, end: str | None) -> None:
    lower, upper = date_bound(start), date_bound(end, end=True)
    if lower and upper and lower > upper:
        raise TdxParamError("开始时间不得晚于结束时间")


@dataclass(frozen=True)
class MarketRequest:
    period: str = '1d'
    count: int | None = None
    start: str | None = None
    end: str | None = None
    fields: list[str] | None = None

    def __post_init__(self):
        normalize_period(self.period)
        validate_dates(self.start, self.end)
        if self.count is not None and (type(self.count) is not int or self.count < 1):
            raise TdxParamError('数量必须为正整数')
        if self.start is not None and self.count is not None:
            raise TdxParamError('--start 与 --count 不可同时指定；日期区间不使用数量截断')

    def sdk_kwargs(self):
        upper = date_bound(self.end, end=True)
        kwargs = {'period': normalize_period(self.period),
                  'count': -1 if self.start is not None else (self.count or 60),
                  'start_time': self.start or '',
                  'end_time': upper.strftime('%Y%m%d%H%M%S') if upper else '',
                  'fill_data': False}
        if self.fields is not None:
            kwargs['field_list'] = self.fields
        return kwargs

    def validate_output(self, data):
        lower, upper = date_bound(self.start), date_bound(self.end, end=True)
        for bars in data.values():
            for bar in bars:
                try:
                    stamp = str(bar['date'])
                    parsed = date_bound(stamp) if re.fullmatch(r'[0-9]{8}|[0-9]{14}', stamp) else pd.Timestamp(stamp)
                    if pd.isna(parsed) or (lower and parsed < lower) or (upper and parsed > upper):
                        raise ValueError('outside requested interval')
                except (ValueError, TypeError, KeyError) as exc:
                    raise TdxDataError('K 线包含无效或超出请求区间的时间，拒绝返回错误区间') from exc


def reformat_kline_data(raw_data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    validate_result(raw_data, 'get_market_data', require_data=True)
    if not isinstance(raw_data, dict):
        raise TdxDataError('K 线响应必须为字段字典')
    fields = {}
    for name, frame in raw_data.items():
        if not isinstance(frame, pd.DataFrame):
            continue
        key = str(name).lower()
        if key in fields:
            raise TdxDataError('K 线响应存在大小写冲突的字段')
        fields[key] = frame
    if not fields:
        raise TdxDataError("K 线响应没有 DataFrame 数据")
    stocks, dates = [], []
    for df in fields.values():
        if not df.index.is_unique or not df.columns.is_unique:
            raise TdxDataError("K 线响应存在重复时间或证券列")
        if df.index.isna().any():
            raise TdxDataError('K 线响应存在空时间')
        stocks.extend(s for s in df.columns if s not in stocks)
        dates.extend(d for d in df.index if d not in dates)
    if not stocks or not dates:
        raise TdxDataError("没有可用的 K 线记录，请检查行情缓存、日期和权限")
    dates = sorted(dates, key=str)

    date_stamps = [d.isoformat() if isinstance(d, (datetime.date, datetime.datetime)) else str(d) for d in dates]
    date_to_pos = {d: idx for idx, d in enumerate(dates)}

    result = {str(stock): [{'date': stamp} for stamp in date_stamps] for stock in stocks}

    for field, df in fields.items():
        for stock in df.columns:
            s = df[stock]
            stock_key = str(stock)
            if pd.api.types.is_bool_dtype(s.dtype):
                raise TdxDataError(f'K 线字段 {field} 包含非数值数据')
            stock_bars = result[stock_key]
            if pd.api.types.is_numeric_dtype(s.dtype):
                arr = s.to_numpy(dtype=float, na_value=math.nan)
                idx_list = s.index.tolist()
                vals = [float(v) if math.isfinite(v) else None for v in arr]
                for dt, cleaned_val in zip(idx_list, vals):
                    stock_bars[date_to_pos[dt]][field] = cleaned_val
            else:
                for dt, val in s.items():
                    if pd.isna(val):
                        cleaned_val = None
                    elif isinstance(val, bool):
                        raise TdxDataError(f'K 线字段 {field} 包含非数值数据')
                    else:
                        try:
                            number = float(val)
                        except (ValueError, TypeError) as exc:
                            raise TdxDataError(f'K 线字段 {field} 包含非数值数据') from exc
                        cleaned_val = number if math.isfinite(number) else None
                    stock_bars[date_to_pos[dt]][field] = cleaned_val

    return result


def pivot_kline_data(
    batch_data: dict[str, list[dict[str, Any]]],
    field: str = 'close',
    fill: str = 'none',
) -> pd.DataFrame:
    """Build a [date x stock] wide DataFrame from formatted kline data."""
    if not isinstance(fill, str) or fill.lower() not in ('none', 'ffill'):
        raise TdxParamError(f"不支持的填充方式 {fill!r}；仅支持 'none' 或 'ffill'")
    fill_mode = fill.lower()

    if not isinstance(field, str) or not field.strip():
        raise TdxParamError("field 必须为非空字符串")
    target_field = field.strip().lower()

    if not isinstance(batch_data, dict) or not batch_data:
        raise TdxParamError("batch_data 必须为非空字典")

    stock_series: dict[str, dict[Any, Any]] = {}
    all_dates: set[Any] = set()
    field_found = False

    for stock_key, bars in batch_data.items():
        if not isinstance(bars, list):
            continue
        series_map: dict[Any, Any] = {}
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            d = bar.get('date')
            if d is None:
                continue
            for k, v in bar.items():
                if k.lower() == target_field:
                    field_found = True
                    series_map[d] = v
                    all_dates.add(d)
                    break
        stock_series[str(stock_key)] = series_map

    if not field_found:
        raise TdxParamError(f"字段 {field!r} 在所有 K 线数据中均不存在")

    sorted_dates = sorted(all_dates, key=str)
    cols = list(batch_data.keys())
    data = {col: [stock_series.get(col, {}).get(d) for d in sorted_dates] for col in cols}

    df = pd.DataFrame(data, index=sorted_dates)
    df.index.name = 'date'

    if fill_mode == 'ffill':
        df = df.ffill()

    return df


def fetch_market_data_chunked(
    tdx: Any,
    stock_list: list[str],
    *,
    request: MarketRequest,
    dividend: str = 'front',
    chunk_size: int | None = None,
    on_progress: Any = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    from .quality import read_batch

    if chunk_size is not None and (isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1):
        raise TdxParamError("chunk_size 必须为正整数")

    if chunk_size is None or len(stock_list) <= chunk_size:
        data, metadata = read_batch(tdx, 'get_market_data', stock_list=stock_list, dividend_type=dividend, **request.sdk_kwargs())
        reformatted = reformat_kline_data(data)
        request.validate_output(reformatted)
        if on_progress:
            on_progress(1, 1, len(stock_list), len(stock_list))
        return reformatted, metadata

    chunks = [stock_list[i:i + chunk_size] for i in range(0, len(stock_list), chunk_size)]
    total_chunks = len(chunks)
    merged_bars: dict[str, list[dict[str, Any]]] = {}
    merged_errors: dict[str, Any] = {}
    last_empty_error: TdxDataError | None = None
    empty_err_markers = ('没有返回可用数据', '没有可用的 K 线记录', 'K 线响应没有 DataFrame 数据')

    for idx, chunk in enumerate(chunks):
        empty_chunk = False
        try:
            data, meta = read_batch(tdx, 'get_market_data', stock_list=chunk, dividend_type=dividend, **request.sdk_kwargs())
        except TdxDataError as exc:
            if not isinstance(exc, TdxReadTimeoutError) and any(m in str(exc) for m in empty_err_markers):
                last_empty_error = exc
                empty_chunk = True
                data = None
                meta = {}
            else:
                raise

        if not empty_chunk:
            if meta and isinstance(meta, dict) and 'errors' in meta:
                merged_errors.update(meta['errors'])
            if data:
                try:
                    part = reformat_kline_data(data)
                    request.validate_output(part)
                    merged_bars.update(part)
                except TdxDataError as exc:
                    if not isinstance(exc, TdxReadTimeoutError) and any(m in str(exc) for m in empty_err_markers):
                        last_empty_error = exc
                    else:
                        raise

        if on_progress:
            on_progress(idx + 1, total_chunks, min((idx + 1) * chunk_size, len(stock_list)), len(stock_list))

    if not merged_bars:
        raise last_empty_error or TdxDataError("get_market_data 没有返回可用数据，请核对证券、缓存、日期和权限")

    return merged_bars, {'errors': merged_errors}


def fetch_snapshots_batch(
    tdx: Any,
    stock_list: list[str],
    *,
    fields: list[str] | None = None,
    allow_partial: bool = False,
    on_progress: Any = None,
) -> dict[str, Any]:
    field_list = fields or []
    snapshots: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    total = len(stock_list)

    for idx, s in enumerate(stock_list):
        try:
            try:
                raw = tdx.get_market_snapshot(s, field_list=field_list)
            except TypeError:
                try:
                    raw = tdx.get_market_snapshot(stock_code=s, field_list=field_list)
                except TypeError:
                    raw = tdx.get_market_snapshot(s)
            snap = validate_result(raw, 'get_market_snapshot', require_data=True)
            snapshots[s] = snap
        except TdxDataError as exc:
            if not allow_partial:
                raise
            errors[s] = {'code': getattr(exc, 'code', 'SDK_DATA_ERROR'), 'message': str(exc)}
        if on_progress:
            on_progress(idx + 1, total, idx + 1, total)

    if not allow_partial:
        return snapshots

    if not snapshots:
        missing = list(stock_list)
        details = {
            'schema_version': 1,
            'success': False,
            'partial': True,
            'requested_stocks': list(stock_list),
            'available_stocks': [],
            'missing_stocks': missing,
            'errors': errors,
            'warnings': ['所有证券实时快照不可用'],
        }
        raise TdxIncompleteDataError("没有可用的实时快照数据", details=details, data={})

    missing = [s for s in stock_list if s not in snapshots]
    return {
        'schema_version': 1,
        'success': True,
        'partial': bool(missing),
        'requested_stocks': list(stock_list),
        'available_stocks': list(snapshots),
        'missing_stocks': missing,
        'errors': errors,
        'warnings': ['部分证券实时快照不可用'] if missing else [],
        'data': snapshots,
    }
