"""Strict, recursive JSON serialization and human-readable output."""
import datetime
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def dataframe_records(df: pd.DataFrame) -> list:
    """Preserve meaningful indexes without exposing a default RangeIndex."""
    if not isinstance(df.index, pd.RangeIndex) or df.index.name is not None:
        df = df.reset_index()
    return df.to_dict('records')


def normalize_json(data: Any) -> Any:
    if data is None or data is pd.NA or data is pd.NaT:
        return None
    if isinstance(data, pd.DataFrame):
        return normalize_json(dataframe_records(data))
    if isinstance(data, pd.Series):
        return normalize_json(data.to_dict())
    if isinstance(data, np.ndarray):
        return normalize_json(data.tolist())
    if isinstance(data, np.generic):
        return normalize_json(data.item())
    if isinstance(data, Mapping):
        return {str(k): normalize_json(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [normalize_json(v) for v in data]
    if isinstance(data, (datetime.datetime, datetime.date, datetime.time)):
        return data.isoformat()
    if isinstance(data, Path):
        return str(data)
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    if isinstance(data, (str, int, bool)):
        return data
    raise TypeError(f"不支持的 JSON 数据类型: {type(data).__name__}")


def to_json(data: Any, indent: int = 2, ensure_ascii: bool = False) -> str:
    return json.dumps(normalize_json(data), indent=indent, ensure_ascii=ensure_ascii, allow_nan=False)


def format_table(data: Any, show_index: bool = True) -> str:
    if isinstance(data, pd.DataFrame):
        return data.to_string(index=show_index)
    if isinstance(data, dict):
        return '\n'.join(f"{k}: {v}" for k, v in data.items())
    if isinstance(data, list):
        return '\n'.join(str(item) for item in data)
    return str(data)


def format_output(data: Any, output_format: str = 'text') -> str:
    return to_json(data) if output_format == 'json' else format_table(data)
