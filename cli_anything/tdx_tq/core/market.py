"""Market requests and lossless normalization, independent of Click."""
import datetime
import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .errors import TdxDataError, TdxParamError
from .sdk import validate_result

SUPPORTED_PERIODS = ('1m', '5m', '10m', '15m', '30m', '1h', '1d', '1w', '1mon', '45d', '1q', '1y')


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

    def __post_init__(self):
        normalize_period(self.period)
        validate_dates(self.start, self.end)
        if self.count is not None and (type(self.count) is not int or self.count < 1):
            raise TdxParamError('数量必须为正整数')
        if self.start is not None and self.count is not None:
            raise TdxParamError('--start 与 --count 不可同时指定；日期区间不使用数量截断')

    def sdk_kwargs(self):
        upper = date_bound(self.end, end=True)
        return {'period': normalize_period(self.period),
                'count': -1 if self.start is not None else (self.count or 60),
                'start_time': self.start or '',
                'end_time': upper.strftime('%Y%m%d%H%M%S') if upper else '',
                'fill_data': False}

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
    result = {}
    for stock in stocks:
        bars = []
        for date in dates:
            stamp = date.isoformat() if isinstance(date, (datetime.date, datetime.datetime)) else str(date)
            bar = {'date': stamp}
            for field, df in fields.items():
                if stock in df.columns and date in df.index:
                    value = df.loc[date, stock]
                    try:
                        number = float(value) if pd.notna(value) else None
                    except (ValueError, TypeError) as exc:
                        raise TdxDataError(f'K 线字段 {field} 包含非数值数据') from exc
                    bar[field] = number if number is not None and math.isfinite(number) else None
            bars.append(bar)
        result[str(stock)] = bars
    return result
