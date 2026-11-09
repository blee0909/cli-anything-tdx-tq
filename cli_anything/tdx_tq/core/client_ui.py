"""Client UI linkages: print_to_tdx panel, send_file, send_bt_data, send_warn."""
import datetime
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import pandas as pd

from .errors import TdxDataError, TdxParamError
from .market import date_bound, stock_code


def _is_safe_filename(name: str) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    if '/' in name or '\\' in name or '..' in name or ':' in name:
        return False
    return os.path.basename(name) == name


def _clean_numeric_str(val: Any) -> str:
    if isinstance(val, bool):
        raise TdxParamError("数值不能为布尔值")
    try:
        f = float(val)
        if not math.isfinite(f):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise TdxParamError(f"无效的数值: {val!r}") from exc
    s = str(val).strip()
    if re.fullmatch(r'^-?[0-9]+(\.[0-9]+)?$', s):
        return s
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


def publish_panel_to_tdx(
    tdx,
    tdx_path: str,
    tables: list[pd.DataFrame],
    *,
    sp_name: str,
    xml_filename: str | None = None,
    jsn_filenames: list[str] | None = None,
    layout: str = 'vertical',
    heights: list[str] | None = None,
    titles: list[str] | None = None,
) -> dict:
    """Publish multiple DataFrames as a custom TDX panel (.xml, .jsn, .sp).

    Validates parameters, guarantees temporary directory isolation for writes,
    and updates the client UI panel in T0002/cloud_cfg, cloud_cache/list, and pad.
    """
    if not isinstance(tables, list) or len(tables) == 0:
        raise TdxParamError("tables 必须是非空 DataFrame 列表")

    for idx, df in enumerate(tables):
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise TdxParamError(f"tables 第 {idx + 1} 个元素必须是非空 DataFrame")
        if df.shape[1] < 2:
            raise TdxParamError(f"tables 第 {idx + 1} 个 DataFrame 列数至少为 2（第一列为日期/键，后续为指标）")

    if not _is_safe_filename(sp_name):
        raise TdxParamError(f"sp_name 格式不安全或包含非法字符: {sp_name!r}")

    clean_layout = layout.strip().lower()
    if clean_layout not in ('vertical', 'horizontal'):
        raise TdxParamError(f"layout 必须为 'vertical' 或 'horizontal'，当前为 {layout!r}")

    group_count = len(tables)

    xml_name = xml_filename or f"{sp_name}.xml"
    if not _is_safe_filename(xml_name) or not xml_name.endswith('.xml'):
        raise TdxParamError(f"xml_filename 必须为安全的 .xml 文件名: {xml_name!r}")

    if jsn_filenames is None:
        jsn_names = [f"{sp_name}_{i + 1}.jsn" for i in range(group_count)]
    else:
        if not isinstance(jsn_filenames, list) or len(jsn_filenames) != group_count:
            raise TdxParamError(f"jsn_filenames 长度 ({len(jsn_filenames) if isinstance(jsn_filenames, list) else 0}) 必须等于 tables 数量 ({group_count})")
        for jsn in jsn_filenames:
            if not _is_safe_filename(jsn) or not jsn.endswith('.jsn'):
                raise TdxParamError(f"jsn_filenames 包含不安全的 .jsn 文件名: {jsn!r}")
        jsn_names = list(jsn_filenames)

    clean_heights = None
    if heights is not None:
        if not isinstance(heights, list) or len(heights) != group_count:
            raise TdxParamError(f"heights 长度必须等于 tables 数量 ({group_count})")
        clean_heights = [str(h) for h in heights]

    clean_titles = None
    if titles is not None:
        if not isinstance(titles, list) or len(titles) != group_count:
            raise TdxParamError(f"titles 长度必须等于 tables 数量 ({group_count})")
        clean_titles = [str(t) for t in titles]

    resolved_tdx = str(Path(tdx_path).resolve())

    raw_obj = getattr(tdx, 'raw', tdx)
    old_tdx_path = getattr(raw_obj, 'tdx_path', None)
    raw_obj.tdx_path = resolved_tdx

    caller = getattr(tdx, 'print_to_tdx', None) or getattr(raw_obj, 'print_to_tdx', None)
    if caller is None:
        raise TdxDataError("TDX SDK 未提供 print_to_tdx 接口")

    v_arg = group_count if clean_layout == 'vertical' else None
    h_arg = group_count if clean_layout == 'horizontal' else None

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp_dir)
                caller(
                    df_list=tables,
                    sp_name=sp_name,
                    xml_filename=xml_name,
                    jsn_filenames=jsn_names,
                    vertical=v_arg,
                    horizontal=h_arg,
                    height=clean_heights,
                    table_names=clean_titles,
                )
            finally:
                os.chdir(old_cwd)
    finally:
        if old_tdx_path is not None:
            raw_obj.tdx_path = old_tdx_path

    sp_file = f"{sp_name}.sp" if sp_name else "python.sp"
    sp_path = str(Path(resolved_tdx) / "T0002" / "pad" / sp_file)
    xml_path = str(Path(resolved_tdx) / "T0002" / "cloud_cfg" / xml_name)
    jsn_paths = [str(Path(resolved_tdx) / "T0002" / "cloud_cache" / "list" / j) for j in jsn_names]

    return {
        'sp_path': sp_path,
        'xml_path': xml_path,
        'jsn_paths': jsn_paths,
        'group_count': group_count,
        'layout': clean_layout,
        'titles': clean_titles or [Path(j).stem for j in jsn_names],
    }


def send_client_file(
    tdx,
    tdx_path: str,
    file_path: str,
    *,
    stage_to_plugins: bool = False,
) -> dict:
    """Send file path to TDX client TQ strategy panel.

    If file resides in or is staged into <tdx_path>/PYPlugins/file, passes filename.
    Otherwise passes resolved absolute path.
    """
    src = Path(file_path).resolve()
    if not src.is_file():
        raise TdxParamError(f"文件不存在: {file_path}")

    plugins_file_dir = Path(tdx_path).resolve() / "PYPlugins" / "file"
    if stage_to_plugins:
        plugins_file_dir.mkdir(parents=True, exist_ok=True)
        dest = plugins_file_dir / src.name
        if src != dest:
            shutil.copy2(src, dest)
        call_arg = src.name
    else:
        try:
            src.relative_to(plugins_file_dir)
            call_arg = src.name
        except ValueError:
            call_arg = str(src)

    caller = getattr(tdx, 'send_file', None) or getattr(getattr(tdx, 'raw', None), 'send_file', None)
    if caller is None:
        raise TdxDataError("TDX SDK 未提供 send_file 接口")

    result = caller(file_path=call_arg)
    return {
        'file': str(src),
        'arg_passed': call_arg,
        'staged': stage_to_plugins,
        'result': result,
    }


def send_client_bt(
    tdx,
    stock: str,
    times: list[str],
    series: list[list[str | float | int]],
    *,
    count: int | None = None,
) -> dict:
    """Send backtest results to TDX client TQ panel."""
    valid_stock = stock_code(stock)

    if not isinstance(times, list) or not times:
        raise TdxParamError("times 必须为非空时间列表")

    for t in times:
        date_bound(t)

    if not isinstance(series, list) or not series:
        raise TdxParamError("series 必须为非空回测数据列表")

    actual_count = count if count is not None else len(times)
    if not isinstance(actual_count, int) or actual_count < 1:
        raise TdxParamError("count 必须为正整数")

    if len(times) < actual_count:
        raise TdxParamError(f"times 长度 ({len(times)}) 小于 count ({actual_count})")
    if len(series) < actual_count:
        raise TdxParamError(f"series 长度 ({len(series)}) 小于 count ({actual_count})")

    cleaned_series = []
    for idx, sub_list in enumerate(series[:actual_count]):
        if not isinstance(sub_list, list) or not (1 <= len(sub_list) <= 16):
            raise TdxParamError(f"series[{idx}] 长度 ({len(sub_list) if isinstance(sub_list, list) else 0}) 非法，必须包含 1 到 16 个数值")
        row = [_clean_numeric_str(val) for val in sub_list]
        cleaned_series.append(row)

    caller = getattr(tdx, 'send_bt_data', None) or getattr(getattr(tdx, 'raw', None), 'send_bt_data', None)
    if caller is None:
        raise TdxDataError("TDX SDK 未提供 send_bt_data 接口")

    result = caller(
        stock_code=valid_stock,
        time_list=times[:actual_count],
        data_list=cleaned_series,
        count=actual_count,
    )

    return {
        'stock': valid_stock,
        'count': actual_count,
        'times': times[:actual_count],
        'series': cleaned_series,
        'result': result,
    }


def send_client_warn(
    tdx,
    stocks: list[str],
    reasons: list[str],
    prices: list[str | float],
    *,
    closes: list[str | float] | None = None,
    volumes: list[str | int | float] | None = None,
    bs_flags: list[str] | None = None,
    warn_types: list[str] | None = None,
    times: list[str] | None = None,
) -> dict:
    """Send alert signals to TDX client TQ panel with BS mapping and safe GBK truncation."""
    if not isinstance(stocks, list) or not stocks:
        raise TdxParamError("stocks 列表不能为空")

    count = len(stocks)
    valid_stocks = [stock_code(s) for s in stocks]

    if not isinstance(reasons, list) or len(reasons) < count:
        raise TdxParamError(f"reasons 列表长度 ({len(reasons) if isinstance(reasons, list) else 0}) 小于 stocks 数量 ({count})")

    if not isinstance(prices, list) or len(prices) < count:
        raise TdxParamError(f"prices 列表长度 ({len(prices) if isinstance(prices, list) else 0}) 小于 stocks 数量 ({count})")

    clean_prices = [_clean_numeric_str(p) for p in prices[:count]]

    if closes is not None:
        if not isinstance(closes, list) or len(closes) < count:
            raise TdxParamError(f"closes 列表长度小于 stocks 数量 ({count})")
        clean_closes = [_clean_numeric_str(c) for c in closes[:count]]
    else:
        clean_closes = list(clean_prices)

    if volumes is not None:
        if not isinstance(volumes, list) or len(volumes) < count:
            raise TdxParamError(f"volumes 列表长度小于 stocks 数量 ({count})")
        clean_volumes = [_clean_numeric_str(v) for v in volumes[:count]]
    else:
        clean_volumes = ['0'] * count

    bs_map = {
        '0': '0', 'b': '0', 'buy': '0',
        '1': '1', 's': '1', 'sell': '1',
        '2': '2', 'neutral': '2', 'unknown': '2',
    }
    if bs_flags is not None:
        if not isinstance(bs_flags, list) or len(bs_flags) < count:
            raise TdxParamError(f"bs_flags 列表长度小于 stocks 数量 ({count})")
        clean_bs = []
        for f in bs_flags[:count]:
            k = str(f).strip().lower()
            if k not in bs_map:
                raise TdxParamError(f"无效的买卖标志 {f!r}；支持 0/B/buy, 1/S/sell, 2/neutral/unknown")
            clean_bs.append(bs_map[k])
    else:
        clean_bs = ['2'] * count

    clean_warn_types = [str(w) for w in warn_types[:count]] if warn_types is not None else ['0'] * count

    if times is not None:
        if not isinstance(times, list) or len(times) < count:
            raise TdxParamError(f"times 列表长度小于 stocks 数量 ({count})")
        clean_times = []
        for t in times[:count]:
            date_bound(t)
            clean_times.append(t)
    else:
        now_str = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        clean_times = [now_str] * count

    # GBK 50 bytes truncation per tdxdata_test.py:286
    clean_reasons = []
    any_truncated = False
    for r in reasons[:count]:
        r_str = str(r)
        encoded = r_str.encode('gbk', errors='ignore')
        if len(encoded) > 50:
            any_truncated = True
            clean_reasons.append(encoded[:50].decode('gbk', errors='ignore'))
        else:
            clean_reasons.append(r_str)

    caller = getattr(tdx, 'send_warn', None) or getattr(getattr(tdx, 'raw', None), 'send_warn', None)
    if caller is None:
        raise TdxDataError("TDX SDK 未提供 send_warn 接口")

    result = caller(
        stock_list=valid_stocks,
        time_list=clean_times,
        price_list=clean_prices,
        close_list=clean_closes,
        volum_list=clean_volumes,
        bs_flag_list=clean_bs,
        warn_type_list=clean_warn_types,
        reason_list=clean_reasons,
        count=count,
    )

    return {
        'count': count,
        'stocks': valid_stocks,
        'reasons': clean_reasons,
        'truncated_reasons': any_truncated,
        'prices': clean_prices,
        'closes': clean_closes,
        'volumes': clean_volumes,
        'bs_flags': clean_bs,
        'result': result,
    }
