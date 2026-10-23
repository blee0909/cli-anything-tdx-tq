"""Explicit completeness contracts for market and financial batch reads."""
import math
import numbers
from dataclasses import dataclass, field

import pandas as pd

from .errors import TdxDataError, TdxIncompleteDataError, TdxParamError
from .sdk import TqAdapter


def parse_fields(value: str) -> list[str]:
    fields = list(dict.fromkeys(part.strip().upper() for part in value.split(',') if part.strip()))
    if not fields:
        raise TdxParamError('请求字段不得为空')
    return fields


def read_batch(sdk, method, **kwargs):
    if isinstance(sdk, TqAdapter):
        return sdk.read_with_metadata(method, **kwargs)
    return getattr(sdk, method)(**kwargs), {}


def available(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return False
    if isinstance(value, str):
        return value.strip() not in ('', '--')
    if isinstance(value, numbers.Real):
        return math.isfinite(value)
    return False


@dataclass
class BatchResult:
    requested: list[str]
    data: dict = field(default_factory=dict)
    missing_fields: dict = field(default_factory=dict)
    missing_values: dict = field(default_factory=dict)
    dropped_records: dict = field(default_factory=dict)
    record_counts: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    unexpected: list = field(default_factory=list)

    @property
    def metadata(self):
        missing = [stock for stock in self.requested if stock not in self.data]
        partial = bool(missing or self.missing_fields or self.missing_values or
                       self.dropped_records or self.errors or self.unexpected)
        warnings = ['部分证券、字段或记录不可用；请检查缓存、日期、字段支持和权限'] if partial else []
        return {'partial': partial, 'requested_stocks': self.requested,
                'available_stocks': list(self.data), 'missing_stocks': missing,
                'missing_fields': self.missing_fields, 'missing_values': self.missing_values,
                'dropped_records': self.dropped_records, 'record_counts': self.record_counts,
                'errors': self.errors, 'unexpected_stocks': self.unexpected, 'warnings': warnings}

    def require(self, allow_partial=False):
        if not self.data or (self.metadata['partial'] and not allow_partial):
            raise TdxIncompleteDataError('请求的数据不完整；有可用数据时可使用 --allow-partial 查看部分结果',
                                         details=self.metadata, data=self.data)
        return self

    def envelope(self):
        return {'schema_version': 1, 'success': True, **self.metadata, 'data': self.data}


def _batch(data, stocks, metadata):
    if not isinstance(data, dict):
        raise TdxDataError('批量读取响应必须为证券字典')
    return BatchResult(list(stocks), errors=dict((metadata or {}).get('errors', {})),
                       unexpected=[str(key) for key in data if key not in stocks])


def assess_market(data, stocks, metadata=None):
    result = _batch(data, stocks, metadata)
    for stock in stocks:
        bars = data.get(stock, [])
        valid = [bar for bar in bars if available(bar.get('close'))]
        result.record_counts[stock] = len(valid)
        if len(valid) != len(bars):
            result.dropped_records[stock] = len(bars) - len(valid)
        if not valid:
            result.missing_fields[stock] = ['close']
        else:
            result.data[stock] = valid
    return result


def _field_map(keys):
    result = {}
    for key in keys:
        normalized = str(key).upper()
        if normalized in result:
            raise TdxDataError('财务响应存在重复或大小写冲突的字段')
        result[normalized] = key
    return result


def assess_financial(data, stocks, fields, metadata=None):
    result = _batch(data, stocks, metadata)
    for stock in stocks:
        value = data.get(stock)
        if value is None:
            result.record_counts[stock] = 0
            result.missing_fields[stock] = list(fields)
            continue
        if not isinstance(value, (pd.DataFrame, dict)):
            raise TdxDataError(f'{stock} 的财务响应必须为 DataFrame 或字段字典')
        lookup = _field_map(value.columns if isinstance(value, pd.DataFrame) else value)
        if isinstance(value, pd.DataFrame):
            if not value.index.is_unique:
                raise TdxDataError('财务响应存在重复索引')
            nrows = len(value)
        else:
            lengths = {len(column) for column in value.values() if isinstance(column, list)}
            if len(lengths) > 1:
                raise TdxDataError('财务响应的字段数组长度不一致')
            nrows = next(iter(lengths), 1 if value else 0)
        valid_rows = [False] * nrows
        missing_fields, missing_values = [], {}
        clean = value.copy()
        for requested in fields:
            key = lookup.get(requested.upper())
            column = value[key] if key is not None else None
            values = (column.tolist() if isinstance(value, pd.DataFrame) and key is not None
                      else column if isinstance(column, list) else [column] * nrows)
            present = [available(cell) for cell in values]
            missing_count = nrows - sum(present)
            if not any(present):
                missing_fields.append(requested)
            if missing_count:
                missing_values[requested] = missing_count
            valid_rows = [old or new for old, new in zip(valid_rows, present)]
            if key is not None:
                normalized = [cell if ok else None for cell, ok in zip(values, present)]
                if isinstance(value, pd.DataFrame):
                    clean[key] = pd.Series(normalized, index=value.index, dtype=object)
                else:
                    clean[key] = normalized if isinstance(column, list) else (normalized[0] if normalized else None)
        if missing_fields:
            result.missing_fields[stock] = missing_fields
        if missing_values:
            result.missing_values[stock] = missing_values
        usable = sum(valid_rows)
        result.record_counts[stock] = usable
        if usable != nrows:
            result.dropped_records[stock] = nrows - usable
        if usable:
            if isinstance(value, pd.DataFrame):
                clean = clean.loc[valid_rows]
            elif usable != nrows:
                clean = {key: [cell for cell, ok in zip(column, valid_rows) if ok]
                         if isinstance(column, list) else column for key, column in clean.items()}
            result.data[stock] = clean
    return result
