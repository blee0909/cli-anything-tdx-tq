"""Atomic exports with consistent date/index and extension semantics."""
import argparse
import os
import tempfile
from pathlib import Path

import pandas as pd

from ..utils.output import dataframe_records, to_json

EXTENSIONS = {'csv': '.csv', 'json': '.json', 'excel': '.xlsx'}


def export_dataframe(df: pd.DataFrame, output_path: str, format: str = 'csv', **kwargs) -> str:
    if format not in EXTENSIONS:
        raise ValueError(f"不支持的格式: {format}")
    path = Path(output_path)
    if path.suffix.lower() != EXTENSIONS[format]:
        raise ValueError(f"{format} 导出需要 {EXTENSIONS[format]} 扩展名")
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
        else:
            df.to_excel(temporary, index=not isinstance(df.index, pd.RangeIndex) or df.index.name is not None,
                        engine='openpyxl')
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
