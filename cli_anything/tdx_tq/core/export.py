"""Atomic exports with consistent date/index and extension semantics."""
import argparse
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

from .errors import TdxParamError
from ..utils.output import dataframe_records, normalize_json, to_json

EXTENSIONS = {'csv': '.csv', 'json': '.json', 'jsonl': '.jsonl', 'excel': '.xlsx', 'parquet': '.parquet'}


def resolve_export_format(output_path: str) -> str:
    suffix = Path(output_path).suffix.lower()
    mapping = {
        '.csv': 'csv',
        '.json': 'json',
        '.jsonl': 'jsonl',
        '.ndjson': 'jsonl',
        '.xlsx': 'excel',
        '.parquet': 'parquet',
    }
    if suffix in mapping:
        return mapping[suffix]
    raise TdxParamError("输出文件扩展名必须为 .csv、.json、.jsonl、.xlsx 或 .parquet")


def export_dataframe(df: pd.DataFrame, output_path: str, format: str = 'csv', **kwargs) -> str:
    if format not in EXTENSIONS:
        raise ValueError(f"不支持的格式: {format}")
    path = Path(output_path)
    suffix = path.suffix.lower()
    expected_ext = EXTENSIONS[format]
    if format == 'jsonl':
        if suffix not in ('.jsonl', '.ndjson'):
            raise ValueError(f"{format} 导出需要 {expected_ext} 扩展名")
    elif suffix != expected_ext:
        raise ValueError(f"{format} 导出需要 {expected_ext} 扩展名")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.export-', suffix=path.suffix)
    os.close(fd)
    temporary = Path(name)
    try:
        if format == 'csv':
            df.to_csv(temporary, index=not isinstance(df.index, pd.RangeIndex) or df.index.name is not None,
                      encoding='utf-8-sig')
        elif format == 'json':
            temporary.write_text(to_json(dataframe_records(df)), encoding='utf-8')
        elif format == 'jsonl':
            records = dataframe_records(df)
            with open(temporary, 'w', encoding='utf-8') as f:
                for row in records:
                    f.write(json.dumps(normalize_json(row), ensure_ascii=False, allow_nan=False) + '\n')
        elif format == 'excel':
            df.to_excel(temporary, index=not isinstance(df.index, pd.RangeIndex) or df.index.name is not None,
                        engine='openpyxl')
        elif format == 'parquet':
            try:
                df.to_parquet(temporary, index=not isinstance(df.index, pd.RangeIndex) or df.index.name is not None)
            except ImportError as exc:
                raise TdxParamError("导出 .parquet 需要安装 pyarrow（例如: uv sync --extra parquet）") from exc
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def export_dict(data: dict, output_dir: str, format: str = 'csv', prefix: str = '') -> dict[str, str]:
    if format not in EXTENSIONS:
        raise ValueError(f"不支持的格式: {format}")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    exported = {}
    for key, df in data.items():
        if isinstance(df, pd.DataFrame):
            stem = f'{prefix}{key}'
            if any(c in stem for c in ('/', '\\', ':')) or stem in ('.', '..'):
                raise ValueError(f"无效导出文件名: {stem}")
            path = output_path / (stem + EXTENSIONS[format])
            exported[key] = export_dataframe(df, str(path), format=format)
    return exported


def create_export_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-o', '--output', required=True, help='输出文件路径')
    parser.add_argument('-f', '--format', choices=tuple(EXTENSIONS), default='csv', help='输出格式')
    return parser
