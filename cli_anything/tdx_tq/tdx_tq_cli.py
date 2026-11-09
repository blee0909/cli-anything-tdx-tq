#!/usr/bin/env python
"""
TDX TQ SDK CLI - 通达信量化接口命令行工具

基于通达信 TQ 策略核心 (D:\\tdx\\PYPlugins\\sys\\tqcenter.py)，
提供完整的行情获取、股票信息、板块分析、财务数据及公式管理命令行工具。
"""

import sys
import os
import json
import time
import logging
import click
import pandas as pd
from typing import Optional, List, Dict, Any
from pathlib import Path

from .core.session import session, TdxError
from .core.errors import TdxDataError, TdxParamError, TdxConfigError, TdxUnsupportedCapabilityError
from .core.local_formula import (
    list_local_formulas,
    get_local_formula_info,
    parse_formula_args,
    parse_formula_statements,
    evaluate_batch_formula,
    RESERVED_SERIES_NAMES,
    CallNode,
    BinaryOpNode,
    UnaryOpNode,
    TDX_FUNCTIONS,
)
from .core.market import (
    reformat_kline_data as _reformat_kline_data, normalize_period, stock_code,
    validate_dates, date_bound, MarketRequest, pivot_kline_data, STOCK_LIST_CATEGORIES,
    fetch_market_data_chunked, fetch_snapshots_batch,
)
from .core.limits import get_limit_summary
from .core.quality import parse_fields, read_batch, assess_market, assess_financial, assess_trading
from .core.trading import OrderRequest, submit_order, cancel_order
from .core.sdk import validate_result
from .utils.cli import JsonGroup, configure_debug, handle_error as _handle_error
from . import __version__
from .core.config import config, discover_tdx_paths
from .core.export import export_dataframe, export_dict, resolve_export_format
from .utils.output import format_output, to_json, format_table, normalize_json
from .core.watch import stream_quotes
from .core.daemon import (
    IN_DAEMON_WORKER, get_daemon_status, start_daemon_background, stop_daemon, serve_daemon
)
from .core.client_ui import (
    publish_panel_to_tdx, send_client_file, send_client_bt, send_client_warn, _clean_numeric_str
)
from .core.local_cache import (
    search_local_stocks, lookup_local_relations, parse_downloaded_file, detect_running_tdx_clients
)


def _parse_stock_list(stocks_input: str) -> List[str]:
    """Accept comma/whitespace separated codes or a JSON/string stdin stream."""
    import re
    raw = sys.stdin.read() if stocks_input.strip() == '-' else stocks_input
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if parsed is not None:
        if not isinstance(parsed, list) or not all(isinstance(s, str) for s in parsed):
            raise TdxParamError("管道 JSON 输入必须为证券代码字符串数组")
        tokens = parsed
    else:
        tokens = re.split(r'[,\s]+', raw)
    codes = list(dict.fromkeys(stock_code(s) for s in tokens if s.strip()))
    if not codes:
        raise TdxParamError("未指定有效的股票代码")
    return codes


@click.group(cls=JsonGroup)
@click.version_option(version=__version__, prog_name='cli-anything-tdx-tq')
@click.option('--debug', is_flag=True, help='向 stderr 输出 SDK 方法和耗时诊断')
@click.option('--tdx-path', help='通达信安装路径 (默认 D:\\tdx)')
@click.option('--timeout', type=float, default=None, help='端到端请求超时秒数 (超时安全终止；写操作返回未知状态且不自动重试)')
@click.option('--no-daemon', is_flag=True, help='跳过本地常驻守护进程，强制在当前进程直连执行')
@click.pass_context
def cli(ctx, tdx_path: Optional[str], debug: bool, timeout: Optional[float] = None, no_daemon: bool = False):
    """
    TDX TQ SDK CLI - 通达信量化接口命令行工具

    基于 TQ 策略模式，提供行情、股票、板块、财务与公式的全功能命令行接口。

    前置条件:
    1. 通达信客户端已启动并登录
    2. 通达信安装在 D:\\tdx 或通过 --tdx-path 指定
    """
    ctx.ensure_object(dict)
    configure_debug(ctx, debug)
    config.override(tdx_path)
    ctx.obj['config'] = config
    def cleanup():
        try:
            if not IN_DAEMON_WORKER.get():
                close_session = getattr(session, 'close', None)
                if callable(close_session):
                    close_session()
        except Exception as exc:
            import logging
            logging.getLogger('cli_anything.tdx_tq').warning('关闭 SDK 会话失败: %s', exc)
        finally:
            config.override(None)
    ctx.call_on_close(cleanup)


@cli.command()
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def init(ctx, as_json: bool):
    """初始化连接（连接仅在当前进程有效）"""
    try:
        session.initialize()
        result = {'success': True, 'run_id': getattr(session.tdx, 'run_id', -1), 'scope': 'process'}
        click.echo(to_json(result) if as_json else f"连接已建立: {result['run_id']} (仅当前进程)")
    except Exception as e:
        _handle_error(ctx, e, as_json, "初始化失败")


@cli.command()
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def close(ctx, as_json: bool):
    """关闭当前进程连接"""
    try:
        session.close()
        click.echo(to_json({'success': True, 'scope': 'process'}) if as_json else "当前进程连接已关闭")
    except Exception as e:
        _handle_error(ctx, e, as_json, "关闭失败")


# ==================== 守护进程命令组 ====================

@cli.group()
def daemon():
    """本地常驻守护进程管理 (启动、停止、状态、前台服务)"""
    pass


@daemon.command('start')
@click.option('--idle-timeout', type=float, default=1800.0, help='空闲自动退出秒数 (默认 1800)')
@click.option('--port', type=int, default=0, help='本地监听端口 (0 为自动分配)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def daemon_start(ctx, idle_timeout: float, port: int, as_json: bool):
    """启动后台常驻守护进程"""
    try:
        if idle_timeout <= 0:
            raise TdxParamError("idle_timeout 必须为正数")
        if not (0 <= port <= 65535):
            raise TdxParamError("port 必须在 0 到 65535 之间")
        st = start_daemon_background(config, idle_timeout=idle_timeout, port=port)
        result = {'success': True, **st}
        if as_json:
            click.echo(to_json(result))
        else:
            click.echo(f"守护进程已启动 (PID: {st.get('pid')}, 端口: {st.get('port')})")
    except Exception as e:
        _handle_error(ctx, e, as_json, "启动守护进程失败")


@daemon.command('stop')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def daemon_stop(ctx, as_json: bool):
    """停止后台常驻守护进程"""
    try:
        res = stop_daemon(config)
        result = {'success': True, **res}
        if as_json:
            click.echo(to_json(result))
        else:
            if res.get('was_running'):
                click.echo(f"守护进程已停止 (PID: {res.get('pid')})")
            else:
                click.echo("守护进程未在运行")
    except Exception as e:
        _handle_error(ctx, e, as_json, "停止守护进程失败")


@daemon.command('status')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def daemon_status(ctx, as_json: bool):
    """查看后台守护进程状态 (离线安全)"""
    try:
        st = get_daemon_status(config)
        if as_json:
            click.echo(to_json(st))
        else:
            click.echo(format_table(st))
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取守护进程状态失败")


@daemon.command('serve')
@click.option('--idle-timeout', type=float, default=1800.0, help='空闲自动退出秒数 (默认 1800)')
@click.option('--port', type=int, default=0, help='本地监听端口 (0 为自动分配)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def daemon_serve(ctx, idle_timeout: float, port: int, as_json: bool):
    """前台运行守护进程服务"""
    try:
        if idle_timeout <= 0:
            raise TdxParamError("idle_timeout 必须为正数")
        if not (0 <= port <= 65535):
            raise TdxParamError("port 必须在 0 到 65535 之间")
        serve_daemon(config, port=port, idle_timeout=idle_timeout)
    except Exception as e:
        _handle_error(ctx, e, as_json, "运行守护服务失败")


# ==================== 行情数据命令组 ====================

@cli.group()
def market():
    """行情数据相关命令 (K线、实时快照、订阅流、除权除息、涨跌停、量价)"""
    pass


@market.command('get')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔或管道输入')
@click.option('--period', '-p', default='1d', help='周期 (60m=1h，默认 1d)')
@click.option('--count', '-c', default=None, type=click.IntRange(min=1), help='尾部数量 (未指定 --start 时默认 60)')
@click.option('--dividend', '-d', type=click.Choice(['none', 'front', 'back']), default='front', help='复权类型 (默认 front)')
@click.option('--start', help='区间开始时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)，与 --count 互斥')
@click.option('--end', help='结束时间 (YYYYMMDD 包含整日，或 YYYYMMDDHHMMSS)')
@click.option('--fields', '-f', help='指定字段，逗号分隔 (如 open,high,low,close,volume)')
@click.option('--pivot', default=None, help='生成 [日期 x 证券] 宽表的目标字段 (如 close, open, volume)')
@click.option('--fill', type=click.Choice(['none', 'ffill'], case_sensitive=False), default='none',
              help='宽表缺失值填充策略 (默认 none；仅搭配 --pivot 使用)')
@click.option('--chunk-size', type=click.IntRange(min=1), default=None,
              help='大批量证券分块拉取大小 (如 80 或 100)，多块时向 stderr 输出进度')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--output', '-o', help='输出文件路径 (.csv, .json, .jsonl, .xlsx, .parquet)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_get(ctx, stocks: str, period: str, count: Optional[int], dividend: str,
               start: Optional[str], end: Optional[str], fields: Optional[str],
               pivot: Optional[str], fill: str, chunk_size: Optional[int],
               allow_partial: bool, output: Optional[str], as_json: bool):
    """获取 K 线；指定 --start 读取日期区间，否则读取尾部数量。"""
    try:
        t_total_start = time.monotonic()
        stock_list = _parse_stock_list(stocks)
        field_list = [f.strip() for f in fields.split(',') if f.strip()] if fields else None

        if fill.lower() != 'none' and not pivot:
            raise TdxParamError("--fill 仅在指定 --pivot 时可用")

        if pivot and field_list is not None:
            pivot_clean = pivot.strip()
            if pivot_clean.lower() not in {f.lower() for f in field_list}:
                field_list.append(pivot_clean)

        request = MarketRequest(period, count, start, end, fields=field_list)
        fmt = None
        if output:
            try:
                fmt = resolve_export_format(output)
            except TdxParamError:
                raise TdxParamError("输出文件扩展名必须为 .csv、.json 或 .xlsx、.jsonl、.parquet")

        def _progress(done_c: int, total_c: int, done_s: int, total_s: int):
            if total_c > 1:
                click.echo(f"[分块进度 {done_c}/{total_c}] 已拉取 {done_s}/{total_s} 只证券", err=True)

        t_read_start = time.monotonic()
        session.ensure_initialized()
        reformatted, metadata = fetch_market_data_chunked(
            session.tdx,
            stock_list,
            request=request,
            dividend=dividend,
            chunk_size=chunk_size,
            on_progress=_progress,
        )
        read_ms = round((time.monotonic() - t_read_start) * 1000.0, 2)

        t_assess_start = time.monotonic()
        batch = assess_market(reformatted, stock_list, metadata, fields=field_list).require(allow_partial)
        assess_ms = round((time.monotonic() - t_assess_start) * 1000.0, 2)

        export_ms = 0.0
        if pivot:
            wide_df = pivot_kline_data(batch.data, field=pivot, fill=fill)
            exported = {}
            if output:
                t_exp_start = time.monotonic()
                export_dataframe(wide_df, output, format=fmt)
                export_ms = round((time.monotonic() - t_exp_start) * 1000.0, 2)
                exported = {'output': output, 'format': fmt, 'records': len(wide_df), 'pivot': pivot, 'fill': fill.lower()}

            total_ms = round((time.monotonic() - t_total_start) * 1000.0, 2)
            logging.getLogger('cli_anything.tdx_tq').debug(
                "market get 阶段耗时: read_ms=%.2f assess_ms=%.2f export_ms=%.2f total_ms=%.2f",
                read_ms, assess_ms, export_ms, total_ms,
            )

            if allow_partial:
                click.echo(to_json({**batch.envelope(), 'pivot': pivot, 'fill': fill.lower(), 'wide_data': wide_df, **exported}))
            elif output:
                click.echo(to_json({'success': True, **exported}) if as_json else f"数据已成功导出至 {output}")
            elif as_json:
                click.echo(to_json(wide_df))
            else:
                click.echo(wide_df.to_string())
        else:
            exported = {}
            if output:
                t_exp_start = time.monotonic()
                rows = [dict(bar, stock=code) for code, bars in batch.data.items() for bar in bars]
                export_dataframe(pd.DataFrame(rows), output, format=fmt)
                export_ms = round((time.monotonic() - t_exp_start) * 1000.0, 2)
                exported = {'output': output, 'format': fmt, 'records': len(rows)}

            total_ms = round((time.monotonic() - t_total_start) * 1000.0, 2)
            logging.getLogger('cli_anything.tdx_tq').debug(
                "market get 阶段耗时: read_ms=%.2f assess_ms=%.2f export_ms=%.2f total_ms=%.2f",
                read_ms, assess_ms, export_ms, total_ms,
            )

            if allow_partial:
                click.echo(to_json({**batch.envelope(), **exported}))
            elif output:
                click.echo(to_json({'success': True, **exported}) if as_json else f"数据已成功导出至 {output}")
            elif as_json:
                click.echo(to_json(batch.data))
            else:
                for code, bars in batch.data.items():
                    click.echo(f"\n=== 股票: {code} ({len(bars)} 条记录) ===")
                    click.echo(pd.DataFrame(bars).to_string(index=False))
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "获取 K 线数据失败")


@market.command('snapshot')
@click.option('--stock', '-s', required=True, help='股票代码，支持单只/多只/管道输入 (例: 600519.SH 或 -)')
@click.option('--fields', '-f', help='指定字段，逗号分隔 (例: Open,High,Low,Close)')
@click.option('--allow-partial', is_flag=True, help='多证券批量快照允许部分成功，返回 schema_version=1 信封')
@click.option('--output', '-o', default=None, help='输出文件路径 (.csv, .json, .jsonl, .xlsx, .parquet)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_snapshot(ctx, stock: str, fields: Optional[str],
                    allow_partial: bool, output: Optional[str], as_json: bool):
    """获取实时五档行情快照 (支持多代码与管道输入)"""
    try:
        stk_list = _parse_stock_list(stock)
        field_list = [f.strip() for f in fields.split(',') if f.strip()] if fields else []
        fmt = resolve_export_format(output) if output else None

        session.ensure_initialized()

        if len(stk_list) == 1 and not allow_partial and not output:
            snapshot = validate_result(session.tdx.get_market_snapshot(stk_list[0], field_list=field_list),
                                       'get_market_snapshot', require_data=True)
            if as_json:
                click.echo(to_json(snapshot, ensure_ascii=False, indent=2))
            else:
                click.echo(f"=== {stk_list[0]} 实时快照 ===")
                for k, v in snapshot.items():
                    click.echo(f"{k}: {v}")
        else:
            res = fetch_snapshots_batch(session.tdx, stk_list, fields=field_list, allow_partial=allow_partial)
            snap_map = res['data'] if allow_partial else res
            exported = {}
            if output:
                rows = [dict(snap, stock=code) for code, snap in snap_map.items()]
                export_dataframe(pd.DataFrame(rows), output, format=fmt)
                exported = {'output': output, 'format': fmt, 'records': len(rows)}
            if allow_partial:
                click.echo(to_json({**res, **exported}))
            elif output:
                click.echo(to_json({'success': True, **exported}) if as_json else f"快照已成功导出至 {output}")
            elif as_json:
                click.echo(to_json(snap_map, ensure_ascii=False, indent=2))
            else:
                for s, snap in snap_map.items():
                    click.echo(f"\n=== {s} 实时快照 ===")
                    for k, v in snap.items():
                        click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "获取行情快照失败")


@market.command('watch')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔或管道输入 (例: 600519.SH,000001.SZ)')
@click.option('--fields', '-f', default=None, help='指定快照字段，逗号分隔')
@click.option('--with-snapshot/--no-snapshot', default=True, help='是否输出初始行情快照 (默认开启)')
@click.option('--debounce-ms', type=click.IntRange(min=0), default=500, help='单股票防抖窗口毫秒数 (默认 500)')
@click.option('--duration', type=float, default=None, help='订阅持续运行秒数 (默认无限制直到中断)')
@click.option('--max-events', type=click.IntRange(min=1), default=None, help='最多接收事件数量')
@click.option('--jsonl', is_flag=True, help='以 compact 单行 NDJSON 格式流式输出')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出事件列表')
@click.pass_context
def market_watch(ctx, stocks: str, fields: Optional[str], with_snapshot: bool,
                 debounce_ms: int, duration: Optional[float], max_events: Optional[int],
                 jsonl: bool, as_json: bool):
    """实时订阅股票行情推送流 (支持防抖、持续时间限制及事件数上限)。"""
    try:
        stock_list = _parse_stock_list(stocks)
        if len(stock_list) > 100:
            raise TdxParamError(f"订阅股票数超过上限 100 (当前 {len(stock_list)})")
        if duration is not None and duration <= 0:
            raise TdxParamError("duration 必须为正数")
        if jsonl and as_json:
            raise TdxParamError("--json 与 --jsonl 互斥，请二选一")

        field_list = [f.strip() for f in fields.split(',') if f.strip()] if fields else None

        session.ensure_initialized()
        stream = stream_quotes(
            session.tdx,
            stock_list,
            with_snapshot=with_snapshot,
            fields=field_list,
            debounce_ms=debounce_ms,
            duration=duration,
            max_events=max_events,
        )

        if jsonl:
            for ev in stream:
                click.echo(json.dumps(normalize_json(ev), ensure_ascii=False))
        elif as_json:
            events = []
            try:
                for ev in stream:
                    events.append(ev)
            except KeyboardInterrupt:
                pass
            click.echo(to_json(events))
        else:
            try:
                for ev in stream:
                    stock_sym = ev.get('stock') or ''
                    seq_num = ev.get('seq', 0)
                    snap = ev.get('snapshot') or {}
                    price = snap.get('Now') or snap.get('now') or snap.get('Close') or ''
                    price_info = f" price={price}" if price else ""
                    click.echo(f"[{ev.get('timestamp')}] #{seq_num} {stock_sym}{price_info}")
            except KeyboardInterrupt:
                pass
    except Exception as e:
        _handle_error(ctx, e, as_json or jsonl, "实时行情订阅失败")


@market.command('divid')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_divid(ctx, stock: str, start: Optional[str], end: Optional[str], as_json: bool):
    """获取除权除息因子数据"""
    try:
        stock = stock_code(stock)
        validate_dates(start, end)
        session.ensure_initialized()
        data = session.tdx.get_divid_factors(
            stock_code=stock.strip(),
            start_time=start or '',
            end_time=end or ''
        )

        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 除权除息数据 ===")
            click.echo(data.to_string() if isinstance(data, pd.DataFrame) else data)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取除权除息数据失败")


@market.command('zdt')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_zdt(ctx, stocks: str, as_json: bool):
    """获取当天涨跌停明细及客户端最近涨停、跌停日期。

    日期来自 ZTDate_Recent/DTDate_Recent，不是全部历史记录。
    官方描述为近两年；客户端可能返回更早日期，此时在 history 的
    outside_documented_window 中标明，范围以客户端为准。
    日期为 null 时检查 history 中的状态：no_record 表示 SDK 返回零值，
    unavailable 表示未提供字段；均不能解释为历史上从未涨跌停。
    不生成全市场连板天梯。
    """
    try:
        stock_list = _parse_stock_list(stocks)
        session.ensure_initialized()
        res = get_limit_summary(session.tdx, stock_list)

        if as_json:
            click.echo(to_json(res, ensure_ascii=False, indent=2))
        else:
            click.echo("=== 当天涨跌停明细与客户端最近事件 ===")
            for code, data in res.items():
                history = data['history']
                up = data['last_limit_up_date'] or history['limit_up_status']
                down = data['last_limit_down_date'] or history['limit_down_status']
                click.echo(f"{code}: 最近涨停日期={up}，最近跌停日期={down} (客户端记录)")
                if history['outside_documented_window']:
                    click.echo("提示：客户端返回了超出官方近两年范围的日期，已保留原值。")
                click.echo(to_json(data, ensure_ascii=False, indent=2))
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取涨跌停数据失败")


@market.command('exday')
@click.option('--stock', '-s', required=True, help='股票代码')
@click.option('--count', '-c', default=10, type=click.IntRange(min=1), help='获取数量 (默认 10)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_exday(ctx, stock: str, count: int, as_json: bool):
    """获取股票日线成交与委托统计；交易日历请使用 stock calendar。"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        data = session.tdx.get_exday_data(stock_code=stock.strip(), count=count)

        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 日线统计 ({len(data)} 条) ===")
            for item in data:
                click.echo(item)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取日线统计数据失败")


@market.command('pv')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_pv(ctx, stocks: str, as_json: bool):
    """获取量价与买卖档位聚合统计"""
    try:
        session.ensure_initialized()
        stock_list = _parse_stock_list(stocks)
        res = session.tdx.get_pricevol(stock_list=stock_list)

        if as_json:
            click.echo(to_json(res, ensure_ascii=False, indent=2))
        else:
            click.echo("=== 量价统计 ===")
            click.echo(res)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取量价统计失败")


@market.command('refresh')
@click.option('--market', '-m', default='AG', help='市场标识 (AG=A股, HK=港股, US=美股, QH=期货, ZS=指数)')
@click.option('--force', is_flag=True, help='是否强制刷新全部缓存')
@click.option('--stocks', '-s', help='刷新指定股票的历史K线缓存，逗号分隔')
@click.option('--period', '-p', default='1d', type=click.Choice(['1m', '5m', '1d'], case_sensitive=False),
              help='历史K线缓存周期 (仅支持 1m, 5m, 1d；默认 1d)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_refresh(ctx, market: str, force: bool, stocks: Optional[str], period: str, as_json: bool):
    """刷新客户端行情缓存。"""
    try:
        stock_list = _parse_stock_list(stocks) if stocks else None
        session.ensure_initialized()
        result = (session.tdx.refresh_kline(stock_list=stock_list, period=period) if stocks
                  else session.tdx.refresh_cache(market=market, force=force))
        click.echo(to_json({'success': True, 'result': result}) if as_json else f"缓存已刷新: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "刷新缓存失败")


# ==================== 股票信息命令组 ====================

@cli.group()
def stock():
    """股票信息相关命令 (基础信息、更多指标、全市场列表、交易日历、代码搜索)"""
    pass


@stock.command('info')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--fields', '-f', help='指定字段，逗号分隔 (例: Name,Unit,VolBase)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_info(ctx, stock: str, fields: Optional[str], as_json: bool):
    """获取股票基础财务与属性信息"""
    try:
        stock = stock_code(stock)
        field_list = [f.strip() for f in fields.split(',') if f.strip()] if fields else []
        session.ensure_initialized()
        info = session.tdx.get_stock_info(stock.strip(), field_list=field_list)

        if as_json:
            click.echo(to_json(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 基础信息 ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取股票信息失败")


@stock.command('more')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--fields', '-f', help='指定字段，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_more(ctx, stock: str, fields: Optional[str], as_json: bool):
    """获取股票进阶信息（主营业务、形态值、安全分等）"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        field_list = [f.strip() for f in fields.split(',')] if fields else []
        info = session.tdx.get_more_info(stock.strip(), field_list=field_list)

        if as_json:
            click.echo(to_json(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 进阶信息 ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取进阶信息失败")


@stock.command('list')
@click.option('--market', '-m', default='23', help='股票集合代码 (5=A股，23=沪深300；默认 23)')
@click.option('--type', '-t', 'list_type', default=0, type=int, help='类型 (0=全部, 1=代码列表)')
@click.option('--categories', is_flag=True, help='离线列出已校准的市场/板块集合代码字典，不建立连接')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_list(ctx, market: str, list_type: int, categories: bool, as_json: bool):
    """获取指定股票集合；默认沪深300，A股集合需指定 -m 5。"""
    try:
        if categories:
            if as_json:
                click.echo(to_json(STOCK_LIST_CATEGORIES, ensure_ascii=False, indent=2))
            else:
                click.echo("=== 市场/板块集合代码字典 ===")
                for code, name in STOCK_LIST_CATEGORIES.items():
                    click.echo(f"{code:>4}: {name}")
            return

        session.ensure_initialized()
        stocks = session.tdx.get_stock_list(market=market, list_type=list_type)

        if as_json:
            click.echo(to_json(stocks, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 股票代码列表 (共 {len(stocks)} 只) ===")
            for s in stocks[:50]:
                click.echo(s)
            if len(stocks) > 50:
                click.echo(f"... 剩余 {len(stocks) - 50} 只省略")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取股票列表失败")


@stock.command('calendar')
@click.option('--market', '-m', default='SH', help='市场标识 (SH, SZ, AG)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--count', '-c', default=20, type=click.IntRange(min=1), help='获取交易日数量 (默认 20)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_calendar(ctx, market: str, start: Optional[str], end: Optional[str], count: int, as_json: bool):
    """查询交易日历与交易日列表"""
    try:
        session.ensure_initialized()
        dates = session.tdx.get_trading_dates(
            market=market,
            start_time=start or '',
            end_time=end or '',
            count=count
        )

        if as_json:
            click.echo(to_json(dates, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 交易日历 ({len(dates)} 天) ===")
            for d in dates:
                click.echo(d)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取交易日历失败")


@stock.command('search')
@click.option('--keyword', '-k', required=True, help='搜索关键词 (如 茅台, 平安, 600519)')
@click.option('--limit', default=50, type=click.IntRange(min=1), help='本地搜索结果条数上限 (默认 50)')
@click.option('--local', is_flag=True, help='仅通过本地行情缓存离线搜索，不连接网络或 SDK')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_search(ctx, keyword: str, limit: int, local: bool, as_json: bool):
    """按名称或代码模糊搜索匹配股票 (支持本地离线缓存及 SDK 联机双通道)"""
    try:
        kw = keyword.strip()
        if not kw:
            raise TdxParamError("搜索关键词不能为空")

        if local:
            results = search_local_stocks(config.tdx_path, kw, limit=limit)
        else:
            has_cap = getattr(session.tdx, 'has_capability', lambda m: hasattr(session.tdx, m))('get_match_stkinfo')
            if has_cap:
                session.ensure_initialized()
                results = session.tdx.get_match_stkinfo(key_word=kw)
            else:
                results = search_local_stocks(config.tdx_path, kw, limit=limit)

        if as_json:
            click.echo(to_json(results, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 搜索 '{kw}' 匹配结果 ({len(results)} 条) ===")
            for item in results:
                click.echo(f"{item.get('Code')}: {item.get('Name')}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "搜索股票失败")


@stock.command('gb')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--count', '-c', default=5, type=click.IntRange(min=1), help='获取记录数 (默认 5)')
@click.option('--dates', '-d', help='指定交易日期列表，逗号分隔 (如 20261020,20261019)')
@click.option('--start', help='开始时间 (YYYYMMDD)，指定区间时与 --count / --dates 互斥')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_gb(ctx, stock: str, count: int, dates: Optional[str],
             start: Optional[str], end: Optional[str], as_json: bool):
    """查询真实日期的股本数据，支持指定日期列表或起止日期区间。"""
    try:
        stock = stock_code(stock)
        if start:
            if dates:
                raise TdxParamError("--start/--end 与 --dates 不可同时指定")
            if ctx.get_parameter_source('count') == click.core.ParameterSource.COMMANDLINE:
                raise TdxParamError("--start/--end 与 --count 不可同时指定")
            validate_dates(start, end)
        else:
            if dates:
                date_list = list(dict.fromkeys(d.strip() for d in dates.split(',') if d.strip()))
                if not date_list:
                    raise TdxParamError("日期列表不能为空")
                for date in date_list:
                    validate_dates(date, None)

        session.ensure_initialized()
        if start:
            data = session.tdx.get_gb_info_by_date(stock_code=stock, start_date=start, end_date=end or '')
        else:
            if not dates:
                date_list = session.get_recent_trading_dates(count=count)
            date_list = date_list[-count:]
            if not date_list:
                raise TdxDataError("没有可用交易日")
            if len(date_list) < count:
                import logging
                logging.getLogger('cli_anything.tdx_tq').warning('仅有 %d 个有效日期，请求 %d 个', len(date_list), count)
            data = session.tdx.get_gb_info(stock_code=stock, date_list=date_list, count=len(date_list))

        click.echo(to_json(data) if as_json else pd.DataFrame(data).to_string(index=False))
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取股本信息失败")


@stock.command('kzz')
@click.option('--stock', '-s', required=True, help='可转债代码 (例: 113050.SH)')
@click.option('--fields', '-f', help='指定字段，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_kzz(ctx, stock: str, fields: Optional[str], as_json: bool):
    """获取可转债基本属性信息"""
    try:
        stock = stock_code(stock)
        field_list = [f.strip() for f in fields.split(',') if f.strip()] if fields else []
        session.ensure_initialized()
        data = session.tdx.get_kzz_info(stock_code=stock.strip(), field_list=field_list)

        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 可转债: {stock} ===")
            if data:
                for k, v in data.items():
                    click.echo(f"{k}: {v}")
            else:
                click.echo("暂无可转债数据或该债券已到期退市")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取可转债信息失败")


@stock.command('relation')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--local', is_flag=True, help='仅通过本地配置缓存文件离线查询板块关系')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_relation(ctx, stock: str, local: bool, as_json: bool):
    """获取股票所属板块关联关系 (所属行业、概念、风格、地区及成分股数量)"""
    try:
        stk = stock_code(stock)
        if local:
            data = lookup_local_relations(config.tdx_path, stk)
        else:
            has_cap = getattr(session.tdx, 'has_capability', lambda m: hasattr(session.tdx, m))('get_relation')
            if has_cap:
                session.ensure_initialized()
                data = session.tdx.get_relation(stock_code=stk)
            else:
                data = lookup_local_relations(config.tdx_path, stk)

        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stk} 所属板块关联关系 ({len(data)} 个) ===")
            if data:
                click.echo(pd.DataFrame(data).to_string(index=False))
            else:
                click.echo("暂无关联板块数据")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取股票关联板块失败")


@stock.command('ipo')
@click.option('--type', '-t', 'ipo_type', default=0, type=click.Choice(['0', '1', '2']),
              help='申购类型 (0=全部, 1=新股, 2=新发债；默认 0)')
@click.option('--date', '-d', 'ipo_date', default=0, type=click.Choice(['0', '1']),
              help='申购时间 (0=今日申购, 1=未来申购；默认 0)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_ipo(ctx, ipo_type: str, ipo_date: str, as_json: bool):
    """获取新股/新债申购信息日历 (今日申购与待申购)"""
    try:
        session.ensure_initialized()
        data = session.tdx.get_ipo_info(ipo_type=int(ipo_type), ipo_date=int(ipo_date))
        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            t_str = {'0': '全部', '1': '新股', '2': '新债'}.get(str(ipo_type), '')
            d_str = {'0': '今日申购', '1': '未来申购'}.get(str(ipo_date), '')
            click.echo(f"=== {t_str} IPO/申购日历 ({d_str}, 共 {len(data)} 条) ===")
            if data:
                click.echo(pd.DataFrame(data).to_string(index=False))
            else:
                click.echo("暂无申购信息")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取申购信息失败")


@stock.command('etf')
@click.option('--index', '-z', 'zs_code', required=True, help='指数代码 (例: 000300.SH 或 399001.SZ)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_etf(ctx, zs_code: str, as_json: bool):
    """获取跟踪指定指数的 ETF 基金列表及其行情、IOPV 净值与规模"""
    try:
        zs_code = stock_code(zs_code)
        session.ensure_initialized()
        data = session.tdx.get_trackzs_etf_info(zs_code=zs_code.strip())
        if as_json:
            click.echo(to_json(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 指数 {zs_code} 跟踪 ETF 基金 ({len(data)} 只) ===")
            if data:
                click.echo(pd.DataFrame(data).to_string(index=False))
            else:
                click.echo("未查找到跟踪该指数的 ETF")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取跟踪 ETF 失败")


# ==================== 板块数据命令组 ====================

@cli.group()
def sector():
    """板块数据相关命令 (板块列表、成分股、自定义板块管理)"""
    pass


SECTOR_CATEGORIES = {
    'all': None,
    'industry': '11',
    'concept': '12',
    'style': '13',
    'region': '14',
    'industry-concept': '15',
    'research1': '16',
    'research2': '17',
    'research3': '18',
    'ext': '110',
}


@sector.command('list')
@click.option('--category', '-c', default='all',
              type=click.Choice(['all', 'industry', 'concept', 'style', 'region', 'industry-concept', 'research1', 'research2', 'research3', 'ext']),
              help='板块类别 (all=全部板块[10], industry=行业[11], concept=概念[12], style=风格[13], region=地区[14], industry-concept=行业+概念[15], research1=研究一[16], research2=研究二[17], research3=研究三[18], ext=扩展板块[110]；默认 all)')
@click.option('--list-type', '--type', '-t', 'list_type', default=0, type=click.IntRange(min=0, max=1),
              help='返回格式 (0=仅代码, 1=代码和名称；--type/-t 为兼容别名)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_list(ctx, category: str, list_type: int, as_json: bool):
    """获取系统板块列表 (支持按分类筛选及代码/名称格式切换)"""
    try:
        session.ensure_initialized()
        market_code = SECTOR_CATEGORIES.get(category)
        if market_code is None:
            sectors = session.tdx.get_sector_list(list_type=int(list_type))
        else:
            sectors = session.tdx.get_stock_list(market=market_code, list_type=int(list_type))

        if as_json:
            click.echo(to_json(sectors, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 板块列表 (共 {len(sectors)} 个) ===")
            for i, sec in enumerate(sectors[:100], 1):
                click.echo(f"{i}. {sec}")
            if len(sectors) > 100:
                click.echo(f"... 剩余 {len(sectors) - 100} 个省略")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取板块列表失败")


@sector.command('stocks')
@click.option('--sector', '-s', required=True, help='板块代码、名称或自定义板块简称 (例: 半导体、ZXG、CSBK)')
@click.option('--block-type', default=0, type=click.IntRange(min=0, max=2),
              help='板块类别 (0=普通板块代码或名称, 1=自定义板块简称, 2=期货；默认 0)')
@click.option('--list-type', '--type', '-t', 'list_type', default=0, type=click.IntRange(min=0, max=1),
              help='返回格式 (0=仅代码, 1=代码和名称；--type/-t 为兼容别名)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_stocks(ctx, sector: str, block_type: int, list_type: int, as_json: bool):
    """获取指定板块的成分股列表"""
    try:
        session.ensure_initialized()
        stocks = session.tdx.get_stock_list_in_sector(
            block_code=sector.strip(), block_type=block_type, list_type=list_type)

        if as_json:
            click.echo(to_json(stocks, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 板块: {sector} (成分股 {len(stocks)} 只) ===")
            for i, stk in enumerate(stocks, 1):
                click.echo(f"{i}. {stk}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取成分股失败")


@sector.command('user')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_user(ctx, as_json: bool):
    """获取当前用户的自选/自定义板块列表"""
    try:
        session.ensure_initialized()
        user_sectors = session.tdx.get_user_sector()

        if as_json:
            click.echo(to_json(user_sectors, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 自选/自定义板块 ({len(user_sectors)} 个) ===")
            for sec in user_sectors:
                click.echo(sec)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取自定义板块失败")


@sector.command('create')
@click.option('--code', '-c', required=True, help='自定义板块英文/字母代码 (如 MYBK)')
@click.option('--name', '-n', required=True, help='自定义板块中文名称 (如 我的自选)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_create(ctx, code: str, name: str, as_json: bool):
    """执行自定义板块操作。"""
    try:
        if not code.strip() or not name.strip():
            raise TdxParamError("板块代码和名称不得为空")
        session.ensure_initialized()
        result = session.tdx.create_sector(block_code=code.strip(), block_name=name.strip())
        click.echo(to_json({'success': True, 'result': result}) if as_json else f"板块操作结果: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "板块操作失败")


@sector.command('delete')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_delete(ctx, code: str, as_json: bool):
    """执行自定义板块操作。"""
    try:
        if not code.strip():
            raise TdxParamError("板块代码和名称不得为空")
        session.ensure_initialized()
        result = session.tdx.delete_sector(block_code=code.strip())
        click.echo(to_json({'success': True, 'result': result}) if as_json else f"板块操作结果: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "板块操作失败")


@sector.command('update-stocks')
@click.option('--code', '-c', default=None, help='自定义板块代码 (如 ZXG 或 自定义代码)')
@click.option('--temp', is_flag=True, help='推送至客户端临时条件股列表 (block_code 为空)')
@click.option('--stocks', '-s', default=None, help='股票列表，逗号分隔或管道输入 (例: 600519.SH,000001.SZ 或 -)')
@click.option('--clear', is_flag=True, help='清空目标板块成分股')
@click.option('--show/--no-show', default=True, help='是否立即在客户端弹出展示该板块 (默认开启)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_update_stocks(ctx, code: Optional[str], temp: bool, stocks: Optional[str],
                         clear: bool, show: bool, as_json: bool):
    """更新自定义板块成分股 (支持临时条件股 --temp 及清空 --clear)。"""
    try:
        has_code = bool(code is not None and code.strip() != '')
        if has_code == bool(temp):
            raise TdxParamError("请指定 --code <板块代码> 或 --temp（二选一）")

        has_stocks = bool(stocks is not None and str(stocks).strip() != '')
        if has_stocks == bool(clear):
            raise TdxParamError("请指定 --stocks <证券列表> 或 --clear（二选一）")

        codes = [] if clear else _parse_stock_list(stocks)
        target_code = '' if temp else code.strip()

        session.ensure_initialized()
        result = session.tdx.send_user_block(block_code=target_code, stock_list=codes, show=show)
        if as_json:
            click.echo(to_json({'success': True, 'result': result, 'count': len(codes),
                                'block_code': target_code, 'show': show}))
        else:
            desc = '临时条件股' if temp else f"板块 {target_code}"
            act = '已清空' if clear else f"已更新 ({len(codes)} 只)"
            click.echo(f"{desc} 成分股{act}: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "更新板块失败")


@sector.command('rename')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.option('--name', '-n', required=True, help='自定义板块新名称')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_rename(ctx, code: str, name: str, as_json: bool):
    """执行自定义板块操作。"""
    try:
        if not code.strip() or not name.strip():
            raise TdxParamError("板块代码和名称不得为空")
        session.ensure_initialized()
        result = session.tdx.rename_sector(block_code=code.strip(), block_name=name.strip())
        click.echo(to_json({'success': True, 'result': result}) if as_json else f"板块操作结果: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "板块操作失败")


@sector.command('clear')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_clear(ctx, code: str, as_json: bool):
    """执行自定义板块操作。"""
    try:
        if not code.strip():
            raise TdxParamError("板块代码和名称不得为空")
        session.ensure_initialized()
        result = session.tdx.clear_sector(block_code=code.strip())
        click.echo(to_json({'success': True, 'result': result}) if as_json else f"板块操作结果: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "板块操作失败")


# ==================== 财务数据命令组 ====================

@cli.group()
def financial():
    """财务与交易统计命令 (专业财务、报告期筛选、交易统计)"""
    pass


@financial.command('get')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--fields', '-f', required=True, help='字段列表，逗号分隔 (如 FN1,FN4,FN6)')
@click.option('--start', help='开始时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)')
@click.option('--end', help='结束时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)')
@click.option('--report-type', type=click.Choice(['report_time', 'announce_time', 'tag_time']), default='report_time',
              help='财务报告期口径 (report_time=报告期, announce_time=公告日PIT, tag_time=标签日；默认 report_time)')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_get(ctx, stocks: str, fields: str, start: Optional[str], end: Optional[str],
                  report_type: str, allow_partial: bool, as_json: bool):
    """获取专业财务报表历史数据，并检查请求字段完整性。"""
    try:
        stock_list = _parse_stock_list(stocks)
        field_list = parse_fields(fields)
        validate_dates(start, end)
        session.ensure_initialized()
        data, metadata = read_batch(session.tdx, 'get_financial_data', stock_list=stock_list,
                                    field_list=field_list, start_time=start or '', end_time=end or '',
                                    report_type=report_type)
        batch = assess_financial(data, stock_list, field_list, metadata).require(allow_partial)
        if allow_partial or as_json:
            click.echo(to_json(batch.envelope() if allow_partial else batch.data))
        else:
            for code, df in batch.data.items():
                click.echo(f"\n=== {code} 财务数据 ===")
                click.echo(df.to_string() if isinstance(df, pd.DataFrame) else df)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "获取财务数据失败")


@financial.command('by-date')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--fields', '-f', required=True, help='字段列表，逗号分隔 (如 FN1,FN4)')
@click.option('--year', '-y', default=0, type=click.IntRange(min=0, max=9999), help='年份 (如 2025，0 表示最新)')
@click.option('--mmdd', '-m', default=0, type=click.Choice(['0', '331', '630', '930', '1231']), help='报告期月日 (0,331,630,930,1231)')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_by_date(ctx, stocks: str, fields: str, year: int, mmdd: str,
                      allow_partial: bool, as_json: bool):
    """按指定报告期日期获取财务数据，并检查请求字段完整性。"""
    try:
        stock_list = _parse_stock_list(stocks)
        field_list = parse_fields(fields)
        session.ensure_initialized()
        data, metadata = read_batch(session.tdx, 'get_financial_data_by_date', stock_list=stock_list,
                                    field_list=field_list, year=year, mmdd=int(mmdd))
        batch = assess_financial(data, stock_list, field_list, metadata).require(allow_partial)
        click.echo(to_json(batch.envelope() if allow_partial else batch.data)
                   if allow_partial or as_json else batch.data)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "按日期获取财务数据失败")


@financial.command('trading')
@click.option('--type', '-t', 'target_type', type=click.Choice(['stock', 'sector', 'market']), default='stock',
              help='交易数据类型')
@click.option('--target', required=False, default='', help='代码 (股票代码列表或板块代码)')
@click.option('--fields', '-f', required=True, help='统计指标字段，逗号分隔 (如 GP01,GP02、BK05,BK06 或 SC01,SC02)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--year', '-y', default=0, type=click.IntRange(min=0, max=9999), help='年份 (如 2024，0 表示最新；与 --start/--end 互斥)')
@click.option('--mmdd', '-m', default='0', type=click.Choice(['0', '331', '630', '930', '1231']), help='报告期月日 (0,331,630,930,1231)')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_trading(ctx, target_type: str, target: str, fields: str,
                      start: Optional[str], end: Optional[str], year: int, mmdd: str,
                      allow_partial: bool, as_json: bool):
    """获取股票/板块/全市场交易统计特征数据"""
    try:
        if target_type in ('stock', 'sector') and not target.strip():
            raise TdxParamError(f"--target 在 -t {target_type} 时为必填项 (例如: 600519.SH 或 板块代码)")
        if (start or end) and (year != 0 or mmdd != '0'):
            raise TdxParamError("--start/--end 日期区间与 --year/--mmdd 报告期不可同时指定")
        validate_dates(start, end)
        session.ensure_initialized()
        field_list = parse_fields(fields)

        targets = []
        by_date = (year != 0 or mmdd != '0')
        if target_type == 'stock':
            targets = _parse_stock_list(target)
            if by_date:
                res = session.tdx.get_gpjy_value_by_date(
                    stock_list=targets,
                    field_list=field_list,
                    year=year,
                    mmdd=int(mmdd)
                )
            else:
                res = session.tdx.get_gpjy_value(
                    stock_list=targets,
                    field_list=field_list,
                    start_time=start or '',
                    end_time=end or ''
                )
        elif target_type == 'sector':
            targets = [s.strip() for s in target.split(',') if s.strip()]
            if not targets:
                raise TdxParamError("板块代码不得为空")
            if by_date:
                res = session.tdx.get_bkjy_value_by_date(
                    stock_list=targets,
                    field_list=field_list,
                    year=year,
                    mmdd=int(mmdd)
                )
            else:
                res = session.tdx.get_bkjy_value(
                    stock_list=targets,
                    field_list=field_list,
                    start_time=start or '',
                    end_time=end or ''
                )
        else:
            targets = ['market']
            if by_date:
                res = session.tdx.get_scjy_value_by_date(
                    field_list=field_list,
                    year=year,
                    mmdd=int(mmdd)
                )
            else:
                res = session.tdx.get_scjy_value(
                    field_list=field_list,
                    start_time=start or '',
                    end_time=end or ''
                )

        checked = assess_trading(res, target_type, targets, field_list, allow_partial=allow_partial)

        if as_json or allow_partial:
            click.echo(to_json(checked, ensure_ascii=False, indent=2))
        else:
            click.echo(checked)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "获取交易统计数据失败")


@financial.command('one')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔或管道输入 (例: 600519.SH,000001.SZ)')
@click.option('--fields', '-f', required=True, help='最新单条财务指标字段，逗号分隔 (如 GO1,GO2 一致预期，GO26 解禁，GO29 机构持仓，GO35 业绩预告)')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_one(ctx, stocks: str, fields: str, allow_partial: bool, as_json: bool):
    """获取股票最新单条财务指标 (一致预期、最新解禁、机构持仓、业绩预告等)"""
    try:
        stock_list = _parse_stock_list(stocks)
        field_list = parse_fields(fields)
        session.ensure_initialized()
        data, metadata = read_batch(session.tdx, 'get_gp_one_data', stock_list=stock_list, field_list=field_list)
        batch = assess_financial(data, stock_list, field_list, metadata).require(allow_partial)
        if allow_partial or as_json:
            click.echo(to_json(batch.envelope() if allow_partial else batch.data))
        else:
            for code, row in batch.data.items():
                click.echo(f"\n=== {code} 最新单条财务指标 ===")
                if isinstance(row, dict):
                    for k, v in row.items():
                        click.echo(f"{k}: {v}")
                else:
                    click.echo(row)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "获取最新财务数据失败")


# ==================== 公式调用与管理命令组 ====================

def _validate_formula_ast_calls(node: Any) -> None:
    """Recursively validate that function calls in formula AST exist and have valid arg counts."""
    if isinstance(node, CallNode):
        func_name = node.func_name
        if func_name not in TDX_FUNCTIONS:
            raise TdxParamError(f"公式调用了未支持的函数: '{func_name}'")
        _, min_args, max_args = TDX_FUNCTIONS[func_name]
        arg_count = len(node.args)
        if not (min_args <= arg_count <= max_args):
            expected = str(min_args) if min_args == max_args else f"{min_args} 到 {max_args}"
            raise TdxParamError(f"函数 {func_name} 需要 {expected} 个参数，但得到了 {arg_count} 个")
        for arg in node.args:
            _validate_formula_ast_calls(arg)
    elif isinstance(node, BinaryOpNode):
        _validate_formula_ast_calls(node.left)
        _validate_formula_ast_calls(node.right)
    elif isinstance(node, UnaryOpNode):
        _validate_formula_ast_calls(node.expr)


def _run_local_formula_subcommand(
    ctx,
    ftype: int,
    name: str,
    args: str,
    stocks: Optional[str],
    period: str,
    count: int,
    return_count: int,
    return_date: bool,
    xsflag: int,
    start: Optional[str],
    end: Optional[str],
    dividend: str,
    chunk_size: Optional[int],
    allow_partial: bool,
    as_json: bool,
):
    if not stocks:
        raise TdxParamError("本地公式计算 (--local) 必须指定 -s/--stocks")
    if not name or not name.strip():
        raise TdxParamError("公式名称不能为空")
    stock_list = _parse_stock_list(stocks)
    if not stock_list:
        raise TdxParamError("证券代码列表不能为空")

    info = get_local_formula_info(name.strip(), ftype)
    parse_formula_args(info['params'], args.strip())
    period = normalize_period(period)
    validate_dates(start, end)
    req_count = None if start else count
    req = MarketRequest(period, req_count, start, end)

    div_map = {'0': 'none', '1': 'front', '2': 'back'}[str(dividend)]

    session.ensure_initialized()
    reformatted, meta = fetch_market_data_chunked(
        session.tdx, stock_list, request=req, dividend=div_map, chunk_size=chunk_size
    )
    batch = assess_market(reformatted, stock_list, meta).require(allow_partial)
    eval_res = evaluate_batch_formula(
        batch.data,
        formula_name=name.strip(),
        formula_type=ftype,
        args_str=args.strip(),
        return_count=return_count,
        return_date=return_date,
        xsflag=xsflag,
        select_mode=(ftype == 1),
    )
    if allow_partial:
        eval_res['completeness'] = batch.metadata

    if as_json or allow_partial:
        click.echo(to_json(eval_res, ensure_ascii=False, indent=2))
    else:
        click.echo(f"=== 本地公式: {name} 计算结果 ===")
        if eval_res.get('selected_count'):
            click.echo(f"选股命中 ({eval_res['selected_count']}): {', '.join(eval_res['selected_stocks'])}")
        if eval_res.get('summary_rows'):
            click.echo(pd.DataFrame(eval_res['summary_rows']).to_string(index=False))
        else:
            click.echo(to_json(eval_res, ensure_ascii=False, indent=2))


@cli.group()
def formula():
    """通达信公式系统命令 (指标公式、选股公式、公式浏览与详情)"""
    pass


@formula.command('list')
@click.option('--type', '-t', 'formula_type', type=click.Choice(['0', '1', '2']), default='0',
              help='公式类型 (0=指标公式，1=选股公式，2=专家公式)')
@click.option('--local', is_flag=True, help='列出内置本地向量化公式库（离线可用，不依赖客户端 C 公式引擎）')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_list(ctx, formula_type: str, local: bool, as_json: bool):
    """获取所有可用通达信指标或选股公式列表"""
    try:
        if local:
            all_formulas = list_local_formulas(int(formula_type))
        else:
            try:
                session.ensure_initialized()
                all_formulas = session.tdx.formula_get_all(formula_type=int(formula_type))
            except TdxUnsupportedCapabilityError as exc:
                raise TdxUnsupportedCapabilityError(
                    f"{exc}；可追加 --local 或使用 formula eval 启用本地向量化公式引擎",
                    details=getattr(exc, 'details', None),
                ) from exc

        if as_json:
            click.echo(to_json(all_formulas, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 通达信公式列表 ({len(all_formulas)} 个) ===")
            for f in all_formulas[:100]:
                click.echo(f)
            if len(all_formulas) > 100:
                click.echo(f"... 剩余 {len(all_formulas) - 100} 个公式省略")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取公式列表失败")


@formula.command('info')
@click.option('--code', '-c', required=True, help='公式名称或代码 (如 MACD, KDJ)')
@click.option('--type', '-t', 'formula_type', default=0, type=click.IntRange(min=0, max=2),
              help='公式类型 (0=指标, 1=选股, 2=专家)')
@click.option('--local', is_flag=True, help='查询内置本地向量化公式库定义（离线可用）')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_info(ctx, code: str, formula_type: int, local: bool, as_json: bool):
    """获取指定公式的详细定义与参数说明"""
    try:
        if not code or not code.strip():
            raise TdxParamError("公式名称或代码不能为空")
        if local:
            info = get_local_formula_info(code.strip(), formula_type)
        else:
            session.ensure_initialized()
            info = session.tdx.formula_get_info(formula_type=formula_type, formula_code=code.strip())

        if as_json:
            click.echo(to_json(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 公式详情: {code} ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取公式详情失败")


@formula.command('zb')
@click.option('--name', '-n', required=True, help='指标公式名称 (如 MACD, KDJ)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔 (如 12,26,9)')
@click.option('--stocks', '-s', help='计算股票列表，逗号分隔 (例: 600519.SH,000001.SZ)')
@click.option('--period', '-p', default='1d', help='周期 (1d, 5m 等)')
@click.option('--count', '-c', default=60, type=click.IntRange(min=1), help='K线根数 (默认 60)')
@click.option('--return-count', default=1, type=click.IntRange(min=0), help='返回记录数 (0 表示全部，默认 1)')
@click.option('--return-date/--no-return-date', default=False, help='是否返回日期字段 (默认 False)')
@click.option('--xsflag', default=-1, type=click.IntRange(min=-1, max=8), help='数据小数位数 (-1 默认精度，0-8 为指定位数)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--dividend', type=click.Choice(['0', '1', '2']), default='0', help='复权类型 (0=不复权, 1=前复权, 2=后复权；默认 0)')
@click.option('--local', is_flag=True, help='使用本地向量化公式引擎基于真实 K 线计算')
@click.option('--chunk-size', type=click.IntRange(min=1), default=None, help='大批量证券分块拉取大小')
@click.option('--allow-partial', is_flag=True, help='允许部分证券数据缺失')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_zb(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int,
               return_count: int, return_date: bool, xsflag: int,
               start: Optional[str], end: Optional[str], dividend: str,
               local: bool, chunk_size: Optional[int], allow_partial: bool, as_json: bool):
    """调用通达信指标公式并计算结果"""
    try:
        if local:
            _run_local_formula_subcommand(
                ctx, 0, name, args, stocks, period, count, return_count, return_date,
                xsflag, start, end, dividend, chunk_size, allow_partial, as_json,
            )
            return

        period = normalize_period(period)
        session.ensure_initialized()
        if stocks:
            validate_dates(start, end)
            stock_list = _parse_stock_list(stocks)
            result = session.tdx.formula_process_mul_zb(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                return_count=return_count,
                return_date=return_date,
                xsflag=xsflag,
                stock_list=stock_list,
                stock_period=period,
                start_time=start or '',
                end_time=end or '',
                count=count,
                dividend_type=int(dividend)
            )
        else:
            result = session.tdx.formula_zb(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                xsflag=xsflag
            )

        if isinstance(result, dict) and 'ErrorId' in result and len(result) > 1:
            result = {k: v for k, v in result.items() if k != 'ErrorId'}

        if as_json:
            click.echo(to_json(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 指标公式: {name} 计算结果 ===")
            click.echo(result)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "调用指标公式失败")


@formula.command('xg')
@click.option('--name', '-n', required=True, help='选股公式名称 (如 UPN, KDJ)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔 (如 3)')
@click.option('--stocks', '-s', help='选股候选股票列表，逗号分隔')
@click.option('--period', '-p', default='1d', help='周期 (1d 等)')
@click.option('--count', '-c', default=60, type=click.IntRange(min=1), help='K线根数 (默认 60)')
@click.option('--return-count', default=1, type=click.IntRange(min=0), help='返回记录数 (0 表示全部，默认 1)')
@click.option('--return-date/--no-return-date', default=False, help='是否返回日期字段 (默认 False)')
@click.option('--xsflag', default=-1, type=click.IntRange(min=-1, max=8), help='数据小数位数 (-1 默认精度，0-8 为指定位数)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--dividend', type=click.Choice(['0', '1', '2']), default='0', help='复权类型 (0=不复权, 1=前复权, 2=后复权；默认 0)')
@click.option('--local', is_flag=True, help='使用本地向量化公式引擎基于真实 K 线计算')
@click.option('--chunk-size', type=click.IntRange(min=1), default=None, help='大批量证券分块拉取大小')
@click.option('--allow-partial', is_flag=True, help='允许部分证券数据缺失')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_xg(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int,
               return_count: int, return_date: bool, xsflag: int,
               start: Optional[str], end: Optional[str], dividend: str,
               local: bool, chunk_size: Optional[int], allow_partial: bool, as_json: bool):
    """调用通达信选股公式筛选股票"""
    try:
        if local:
            _run_local_formula_subcommand(
                ctx, 1, name, args, stocks, period, count, return_count, return_date,
                xsflag, start, end, dividend, chunk_size, allow_partial, as_json,
            )
            return

        period = normalize_period(period)
        session.ensure_initialized()
        if stocks:
            validate_dates(start, end)
            stock_list = _parse_stock_list(stocks)
            result = session.tdx.formula_process_mul_xg(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                return_count=return_count,
                return_date=return_date,
                stock_list=stock_list,
                stock_period=period,
                start_time=start or '',
                end_time=end or '',
                count=count,
                dividend_type=int(dividend)
            )
        else:
            result = session.tdx.formula_xg(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                xsflag=xsflag
            )

        if isinstance(result, dict) and 'ErrorId' in result and len(result) > 1:
            result = {k: v for k, v in result.items() if k != 'ErrorId'}

        if as_json:
            click.echo(to_json(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 选股公式: {name} 筛选结果 ===")
            click.echo(result)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "调用选股公式失败")


@formula.command('exp')
@click.option('--name', '-n', required=True, help='专家系统公式名称 (如 BBI, MACD)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔')
@click.option('--stocks', '-s', help='计算股票列表，逗号分隔 (例: 600519.SH,000001.SZ)')
@click.option('--period', '-p', default='1d', help='周期 (1d, 5m 等)')
@click.option('--count', '-c', default=60, type=click.IntRange(min=1), help='K线根数 (默认 60)')
@click.option('--return-count', default=1, type=click.IntRange(min=0), help='返回记录数 (0 表示全部，默认 1)')
@click.option('--return-date/--no-return-date', default=False, help='是否返回日期字段 (默认 False)')
@click.option('--xsflag', default=-1, type=click.IntRange(min=-1, max=8), help='数据小数位数 (-1 默认精度，0-8 为指定位数)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--dividend', type=click.Choice(['0', '1', '2']), default='0', help='复权类型 (0=不复权, 1=前复权, 2=后复权；默认 0)')
@click.option('--local', is_flag=True, help='使用本地向量化公式引擎基于真实 K 线计算')
@click.option('--chunk-size', type=click.IntRange(min=1), default=None, help='大批量证券分块拉取大小')
@click.option('--allow-partial', is_flag=True, help='允许部分证券数据缺失')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_exp(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int,
                return_count: int, return_date: bool, xsflag: int,
                start: Optional[str], end: Optional[str], dividend: str,
                local: bool, chunk_size: Optional[int], allow_partial: bool, as_json: bool):
    """调用通达信专家系统公式计算买入/卖出信号与评测结果"""
    try:
        if local:
            _run_local_formula_subcommand(
                ctx, 2, name, args, stocks, period, count, return_count, return_date,
                xsflag, start, end, dividend, chunk_size, allow_partial, as_json,
            )
            return

        period = normalize_period(period)
        session.ensure_initialized()
        if stocks:
            validate_dates(start, end)
            stock_list = _parse_stock_list(stocks)
            result = session.tdx.formula_process_mul_exp(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                return_count=return_count,
                return_date=return_date,
                xsflag=xsflag,
                stock_list=stock_list,
                stock_period=period,
                start_time=start or '',
                end_time=end or '',
                count=count,
                dividend_type=int(dividend)
            )
        else:
            result = session.tdx.formula_exp(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                xsflag=xsflag
            )

        if isinstance(result, dict) and 'ErrorId' in result and len(result) > 1:
            result = {k: v for k, v in result.items() if k != 'ErrorId'}

        if as_json:
            click.echo(to_json(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 专家系统公式: {name} 计算结果 ===")
            click.echo(result)
    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "调用专家系统公式失败")


@formula.command('eval')
@click.option('--expr', '-e', default=None,
              help='通达信公式表达式或源码 (支持 {注释}、:= 中间变量、: 输出线)')
@click.option('--formula-file', default=None,
              help='从本地文件 (.tdx/.txt) 读取通达信公式源码')
@click.option('--name', '-n', default=None,
              help='调用内置公式名称 (如 MACD, KDJ, MA_CROSS, VOL_BREAK)')
@click.option('--type', '-t', 'formula_type', type=click.Choice(['0', '1', '2']), default=None,
              help='内置公式类别限定 (0=指标, 1=选股, 2=专家)')
@click.option('--args', '-a', default='',
              help='公式参数 (内置公式支持位置或命名如 12,26,9；自定义表达式用 N=5,M=20)')
@click.option('--stocks', '-s', default=None,
              help='证券代码列表，逗号分隔或 - 从 stdin 读取')
@click.option('--sector', default=None,
              help='直接对指定通达信板块成分股计算 (如 半导体、880381.SH、CSBK)')
@click.option('--block-type', type=click.IntRange(min=0, max=2), default=0,
              help='搭配 --sector 使用的板块类型 (0=系统板块, 1=自定义板块简称, 2=期货)')
@click.option('--market-list', default=None,
              help='直接对指定市场集合代码计算 (如 0=自选股, 1=持仓股, 23=沪深300, 5=全部A股)')
@click.option('--period', '-p', default='1d', help='K线周期 (默认 1d)')
@click.option('--count', '-c', type=click.IntRange(min=1), default=None,
              help='尾部 K 线根数 (未指定 --start 时默认 60)')
@click.option('--start', default=None,
              help='区间开始时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)，与 --count 互斥')
@click.option('--end', default=None,
              help='结束时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)')
@click.option('--dividend', '-d', type=click.Choice(['none', 'front', 'back']), default='front',
              help='复权类型 (默认 front)')
@click.option('--chunk-size', type=click.IntRange(min=1), default=None,
              help='大批量证券分块拉取大小 (如 80 或 100)')
@click.option('--return-count', type=click.IntRange(min=0), default=1,
              help='每只证券返回的最近期数 (0=全部，默认 1)')
@click.option('--return-date/--no-return-date', default=True,
              help='是否包含日期序列 (默认开启)')
@click.option('--xsflag', type=click.IntRange(min=-1, max=8), default=4,
              help='数值保留小数位 (-1 不舍入，默认 4)')
@click.option('--select/--no-select', default=False,
              help='选股筛选模式：以最后一条输出线 > 0 判定命中信号')
@click.option('--allow-partial', is_flag=True,
              help='允许部分证券停牌或无 K 线数据，仅对可用证券计算')
@click.option('--push-sector', default=None,
              help='将选股命中的证券同步写入指定自定义板块简称 (如 AIPOOL)')
@click.option('--push-temp', is_flag=True,
              help='将选股命中的证券同步推送至通达信客户端临时条件股')
@click.option('--show/--no-show', default=True,
              help='推送板块/临时条件股时是否在客户端主动展示窗口 (默认开启)')
@click.option('--push-panel', default=None,
              help='将汇总结果发布为通达信自定义看盘面板 (.sp 名称)')
@click.option('--push-warn', is_flag=True,
              help='将选股命中的证券作为买入信号推送至通达信 TQ 预警面板')
@click.option('--warn-reason', default=None,
              help='搭配 --push-warn 使用的自定义预警原因说明')
@click.option('--output', '-o', default=None,
              help='导出汇总结果表至文件 (.csv, .json, .jsonl, .xlsx, .parquet)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_eval(
    ctx,
    expr: Optional[str],
    formula_file: Optional[str],
    name: Optional[str],
    formula_type: Optional[str],
    args: str,
    stocks: Optional[str],
    sector: Optional[str],
    block_type: int,
    market_list: Optional[str],
    period: str,
    count: Optional[int],
    start: Optional[str],
    end: Optional[str],
    dividend: str,
    chunk_size: Optional[int],
    return_count: int,
    return_date: bool,
    xsflag: int,
    select: bool,
    allow_partial: bool,
    push_sector: Optional[str],
    push_temp: bool,
    show: bool,
    push_panel: Optional[str],
    push_warn: bool,
    warn_reason: Optional[str],
    output: Optional[str],
    as_json: bool,
):
    """本地向量化通达信公式评测与闭环量化执行 (支持指标评测、条件选股、推送自选/临时板块/面板/预警)"""
    try:
        # 1. Exact one formula source
        formula_sources = [('expr', expr), ('formula_file', formula_file), ('name', name)]
        specified_f = [k for k, v in formula_sources if v is not None and str(v).strip() != '']
        if len(specified_f) != 1:
            raise TdxParamError("必须且仅能指定 --expr、--formula-file 或 -n/--name 之一")

        # 2. Exact one universe source
        universe_sources = [('stocks', stocks), ('sector', sector), ('market_list', market_list)]
        specified_u = [k for k, v in universe_sources if v is not None and str(v).strip() != '']
        if len(specified_u) != 1:
            raise TdxParamError("必须且仅能指定 -s/--stocks、--sector 或 --market-list 之一")

        # 3. Reject push_sector + push_temp together; validate push_sector
        if push_sector and push_temp:
            raise TdxParamError("--push-sector 与 --push-temp 互斥，不能同时使用")
        if push_sector is not None and not push_sector.strip():
            raise TdxParamError("--push-sector 板块名称不能为空")

        # 4. Export format validation before connection
        fmt = None
        if output:
            try:
                fmt = resolve_export_format(output)
            except TdxParamError:
                raise TdxParamError("输出文件扩展名必须为 .csv、.json 或 .xlsx、.jsonl、.parquet")

        # 5. MarketRequest & date validation before connection
        period = normalize_period(period)
        validate_dates(start, end)
        if start and count is not None:
            raise TdxParamError("--start 与 --count 互斥，不可同时指定")
        req_count = count if count is not None else (None if start else 60)
        request = MarketRequest(period, req_count, start, end)

        # 6. Stocks parsing if --stocks before connection
        stock_list = None
        if stocks is not None:
            stock_list = _parse_stock_list(stocks)
            if not stock_list:
                raise TdxParamError("证券代码列表不能为空")

        # 7. Resolve formula & args syntax validation before connection
        resolved_expr = None
        builtin_info = None
        int_ftype = int(formula_type) if formula_type is not None else None

        if formula_file:
            fpath = Path(formula_file).resolve()
            if not fpath.is_file():
                raise TdxParamError(f"公式文件不存在: {formula_file}")
            try:
                resolved_expr = fpath.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                resolved_expr = fpath.read_text(encoding='gbk')
        elif expr:
            resolved_expr = expr.strip()

        if name:
            builtin_info = get_local_formula_info(name.strip(), int_ftype)
            parse_formula_args(builtin_info['params'], args.strip())

        effective_select = bool(
            select or push_sector or push_temp or push_warn or (builtin_info and builtin_info['formula_type'] == 1)
        )

        if resolved_expr:
            stmts = parse_formula_statements(resolved_expr, select_mode=effective_select)
            for stmt in stmts:
                _validate_formula_ast_calls(stmt.expr)
            if args and args.strip():
                for part in args.split(','):
                    part = part.strip()
                    if not part:
                        continue
                    if '=' not in part:
                        raise TdxParamError("自定义表达式的 args 参数必须使用命名格式 (如 'N=5,M=10')")
                    k, v = part.split('=', 1)
                    k_clean = k.strip().upper()
                    if k_clean in RESERVED_SERIES_NAMES:
                        raise TdxParamError(f"参数名称 '{k_clean}' 与系统保留字段冲突，不允许覆盖")
                    try:
                        float(v.strip())
                    except ValueError:
                        raise TdxParamError(f"参数 '{k.strip()}' 的值无效: '{v.strip()}'，必须为有效数值")

        # Pre-connection validation complete. Now connect!
        session.ensure_initialized()

        # Universe resolution if sector or market_list
        if sector is not None:
            raw_codes = session.tdx.get_stock_list_in_sector(
                block_code=sector.strip(), block_type=block_type, list_type=0
            )
            stock_list = [
                str(c['Code'] if isinstance(c, dict) and 'Code' in c else c).strip()
                for c in (raw_codes or [])
                if c
            ]
            if not stock_list:
                raise TdxDataError(f"板块 '{sector}' 成分股为空")
        elif market_list is not None:
            raw_codes = session.tdx.get_stock_list(market=market_list.strip(), list_type=0)
            stock_list = [
                str(c['Code'] if isinstance(c, dict) and 'Code' in c else c).strip()
                for c in (raw_codes or [])
                if c
            ]
            if not stock_list:
                raise TdxDataError(f"市场集合 '{market_list}' 证券列表为空")

        def _progress(done_c: int, total_c: int, done_s: int, total_s: int):
            if total_c > 1:
                click.echo(f"[分块进度 {done_c}/{total_c}] 已拉取 {done_s}/{total_s} 只证券", err=True)

        reformatted, metadata = fetch_market_data_chunked(
            session.tdx,
            stock_list,
            request=request,
            dividend=dividend,
            chunk_size=chunk_size,
            on_progress=_progress,
        )
        batch = assess_market(reformatted, stock_list, metadata).require(allow_partial)

        eval_out = evaluate_batch_formula(
            batch.data,
            expr=resolved_expr if not name else None,
            formula_name=name.strip() if name else None,
            formula_type=int_ftype,
            args_str=args.strip(),
            return_count=return_count,
            return_date=return_date,
            xsflag=xsflag,
            select_mode=effective_select,
        )

        actions: dict[str, Any] = {}

        # 1. Export (-o)
        if output:
            df_summary = pd.DataFrame(eval_out['summary_rows'])
            export_dataframe(df_summary, output, format=fmt)
            actions['export'] = {
                'output': output,
                'format': fmt,
                'records': len(df_summary),
            }

        # 2. Push Sector / Temp (--push-sector / --push-temp)
        if push_sector or push_temp:
            target_block = '' if push_temp else push_sector.strip()
            if eval_out['selected_stocks']:
                blk_res = session.tdx.send_user_block(
                    block_code=target_block, stock_list=eval_out['selected_stocks'], show=show
                )
                actions['sector_push'] = {
                    'pushed': True,
                    'block_code': target_block,
                    'temp': bool(push_temp),
                    'count': eval_out['selected_count'],
                    'result': blk_res,
                }
            else:
                actions['sector_push'] = {
                    'pushed': False,
                    'block_code': target_block,
                    'reason': 'no_selected_stocks',
                    'count': 0,
                }

        # 3. Push Panel (--push-panel)
        if push_panel:
            panel_rows = (
                [r for r in eval_out['summary_rows'] if r.get('hit')]
                if effective_select and eval_out['selected_stocks']
                else eval_out['summary_rows']
            )
            df_panel = pd.DataFrame(panel_rows)
            if df_panel.empty:
                df_panel = pd.DataFrame({'date': [], 'stock': []})
            panel_res = publish_panel_to_tdx(
                session.tdx,
                config.tdx_path,
                [df_panel],
                sp_name=push_panel.strip(),
                titles=[f"{eval_out['formula']} ({len(df_panel)})"],
            )
            actions['panel_push'] = panel_res

        # 4. Push Warn (--push-warn)
        if push_warn:
            if eval_out['selected_stocks']:
                reason = warn_reason or f"公式选股命中:{eval_out['formula']}"
                prices = []
                closes = []
                volumes = []
                for s in eval_out['selected_stocks']:
                    bars_s = batch.data.get(s, [])
                    last_b = bars_s[-1] if isinstance(bars_s, list) and bars_s else (
                        bars_s.iloc[-1].to_dict() if isinstance(bars_s, pd.DataFrame) and not bars_s.empty else {}
                    )
                    p = last_b.get('close', 0.0) or 0.0
                    v = last_b.get('volume', 0.0) or last_b.get('vol', 0.0) or 0.0
                    prices.append(p)
                    closes.append(p)
                    volumes.append(v)
                warn_res = send_client_warn(
                    session.tdx,
                    stocks=eval_out['selected_stocks'],
                    reasons=[reason] * len(eval_out['selected_stocks']),
                    prices=prices,
                    closes=closes,
                    volumes=volumes,
                    bs_flags=['0'] * len(eval_out['selected_stocks']),
                )
                actions['warn_push'] = warn_res
            else:
                actions['warn_push'] = {
                    'pushed': False,
                    'reason': 'no_selected_stocks',
                    'count': 0,
                }

        if allow_partial:
            eval_out['completeness'] = batch.metadata
        if actions:
            eval_out['actions'] = actions

        if as_json or allow_partial:
            click.echo(to_json(eval_out, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 本地公式评测: {eval_out['formula']} (共 {eval_out['total_evaluated']} 只) ===")
            if effective_select:
                click.echo(f"选股命中 ({eval_out['selected_count']}): {', '.join(eval_out['selected_stocks']) if eval_out['selected_stocks'] else '无'}")
            if eval_out['summary_rows']:
                click.echo(pd.DataFrame(eval_out['summary_rows']).to_string(index=False))
            if actions:
                click.echo(f"\n执行操作动作: {to_json(actions, ensure_ascii=False, indent=2)}")

    except Exception as e:
        _handle_error(ctx, e, as_json or allow_partial, "公式评测失败")


# ==================== 客户端联动与预警命令组 ====================

@cli.group()
def client():
    """通达信桌面端 GUI 联动与预警通知 (联动看盘、预警弹窗、文件推送、回测推送、自定义面板)"""
    pass


@client.command('link')
@click.option('--stock', '-s', required=True, help='联动跳转的股票代码 (例: 600519.SH)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_link(ctx, stock: str, as_json: bool):
    """联动通达信客户端窗口切换并展示指定股票 K 线行情"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        stk_clean = stock.strip()
        code_pure = stk_clean.split('.')[0] if '.' in stk_clean else stk_clean
        url = f"tdx://stock?code={code_pure}"
        res = session.tdx.exec_to_tdx(url=url)
        if as_json:
            click.echo(to_json({"success": True, "action": "link", "stock": stk_clean, "raw": res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"已向通达信客户端发送联动看盘指令 -> {stk_clean}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "联动客户端失败")


@client.command('warn')
@click.option('--stock', '-s', required=True, help='预警股票代码，多只逗号分隔 (例: 600519.SH,000001.SZ)')
@click.option('--message', '-m', required=True, help='预警提示内容，多条以 | 分隔')
@click.option('--price', '-p', default='0.0', help='当前触发价格，多值逗号分隔')
@click.option('--close', default=None, help='昨收价，多值逗号分隔 (默认等于 price)')
@click.option('--volume', default=None, help='预警触发成交量，多值逗号分隔 (默认 0)')
@click.option('--bs-flag', default='0', help='买卖方向标识 (0/B/buy, 1/S/sell, 2/neutral；默认 0)')
@click.option('--warn-type', default='1', help='预警类型 (默认 1)')
@click.option('--time', 'warn_time', default=None, help='预警时间戳 YYYYMMDDHHMMSS，多值逗号分隔 (默认当前时间)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_warn(ctx, stock: str, message: str, price: str, close: Optional[str],
                volume: Optional[str], bs_flag: str, warn_type: str,
                warn_time: Optional[str], as_json: bool):
    """向通达信桌面端发送右下角交易预警弹窗 (支持批量与买卖方向映射)。"""
    try:
        stocks_list = _parse_stock_list(stock)
        n = len(stocks_list)

        reasons = [r.strip() for r in message.split('|')]
        if len(reasons) == 1 and n > 1:
            reasons = reasons * n

        prices = [p.strip() for p in price.split(',') if p.strip()]
        if len(prices) == 1 and n > 1:
            prices = prices * n

        closes = None
        if close is not None:
            closes = [c.strip() for c in close.split(',') if c.strip()]
            if len(closes) == 1 and n > 1:
                closes = closes * n

        volumes = None
        if volume is not None:
            volumes = [v.strip() for v in volume.split(',') if v.strip()]
            if len(volumes) == 1 and n > 1:
                volumes = volumes * n

        bs_flags = [b.strip() for b in bs_flag.split(',') if b.strip()]
        if len(bs_flags) == 1 and n > 1:
            bs_flags = bs_flags * n

        warn_types = [w.strip() for w in warn_type.split(',') if w.strip()]
        if len(warn_types) == 1 and n > 1:
            warn_types = warn_types * n

        times = None
        if warn_time is not None:
            times = [t.strip() for t in warn_time.split(',') if t.strip()]
            if len(times) == 1 and n > 1:
                times = times * n

        if len(reasons) < n:
            raise TdxParamError(f"reasons 列表长度 ({len(reasons)}) 小于 stocks 数量 ({n})")
        if len(prices) < n:
            raise TdxParamError(f"prices 列表长度 ({len(prices)}) 小于 stocks 数量 ({n})")
        for p_val in prices[:n]:
            _clean_numeric_str(p_val)
        if closes is not None:
            if len(closes) < n:
                raise TdxParamError(f"closes 列表长度小于 stocks 数量 ({n})")
            for c_val in closes[:n]:
                _clean_numeric_str(c_val)
        if volumes is not None:
            if len(volumes) < n:
                raise TdxParamError(f"volumes 列表长度小于 stocks 数量 ({n})")
            for v_val in volumes[:n]:
                _clean_numeric_str(v_val)
        if times is not None:
            if len(times) < n:
                raise TdxParamError(f"times 列表长度小于 stocks 数量 ({n})")
            for t_val in times[:n]:
                date_bound(t_val)

        session.ensure_initialized()
        res = send_client_warn(
            session.tdx,
            stocks=stocks_list,
            reasons=reasons,
            prices=prices,
            closes=closes,
            volumes=volumes,
            bs_flags=bs_flags,
            warn_types=warn_types,
            times=times,
        )
        if as_json:
            click.echo(to_json({"success": True, "action": "warn", **res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"预警已发送至客户端 ({res['count']} 条): {', '.join(res['stocks'])}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "发送预警失败")


@client.command('msg')
@click.option('--message', '-m', required=True, help='消息内容')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_msg(ctx, message: str, as_json: bool):
    """向通达信桌面端发送文字通知消息"""
    try:
        session.ensure_initialized()
        res = session.tdx.send_message(msg_str=message.strip())
        if as_json:
            click.echo(to_json({"success": True, "action": "msg", "message": message, "raw": res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"消息已发送至客户端: {message}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "发送消息失败")


@client.command('file')
@click.option('--file', '-f', 'file_path', required=True, help='要推送给客户端的文件路径')
@click.option('--stage/--no-stage', default=False, help='是否先暂存至 PYPlugins 目录再推送 (默认 False)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_file(ctx, file_path: str, stage: bool, as_json: bool):
    """向通达信桌面端推送指定本地文件 (可选暂存至 PYPlugins)。"""
    try:
        p = Path(file_path)
        if not p.is_file():
            raise TdxParamError(f"文件不存在: {file_path}")
        session.ensure_initialized()
        res = send_client_file(session.tdx, config.tdx_path, str(p), stage_to_plugins=stage)
        if as_json:
            click.echo(to_json({"success": True, "action": "file", **res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"文件已推送至客户端: {p} (staged={stage})", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "推送文件失败")


@client.command('bt')
@click.option('--stock', '-s', required=True, help='回测关联的股票代码 (例: 600519.SH)')
@click.option('--times', required=True, help='回测时间点列表，逗号分隔 (例: 20261019,20261020)')
@click.option('--data', required=True, help='回测指标序列数据，支持 JSON 二维数组或分号/逗号格式 (如 1.0,2.0;1.1,2.1)')
@click.option('--count', '-c', default=None, type=click.IntRange(min=1), help='发送回测数据条数 (默认与 times 长度一致)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_bt(ctx, stock: str, times: str, data: str, count: Optional[int], as_json: bool):
    """向通达信桌面端 TQ 面板推送策略回测指标数据序列。"""
    try:
        valid_stock = stock_code(stock)
        time_list = [t.strip() for t in times.split(',') if t.strip()]
        if not time_list:
            raise TdxParamError("times 不能为空")
        for t in time_list:
            date_bound(t)

        raw_data = data.strip()
        if raw_data.startswith('['):
            try:
                series = json.loads(raw_data)
            except Exception as exc:
                raise TdxParamError(f"解析 JSON 回测数据失败: {exc}") from exc
        else:
            series = []
            rows = [r.strip() for r in raw_data.split(';') if r.strip()]
            for r in rows:
                cols = [c.strip() for c in r.split(',') if c.strip()]
                series.append(cols)

        if not isinstance(series, list) or not series:
            raise TdxParamError("回测数据 series 不能为空")

        actual_count = count if count is not None else len(time_list)
        if len(time_list) < actual_count:
            raise TdxParamError(f"times 长度 ({len(time_list)}) 小于 count ({actual_count})")
        if len(series) < actual_count:
            raise TdxParamError(f"series 长度 ({len(series)}) 小于 count ({actual_count})")
        for idx, sub_list in enumerate(series[:actual_count]):
            if not isinstance(sub_list, list) or not (1 <= len(sub_list) <= 16):
                raise TdxParamError(f"series[{idx}] 长度 ({len(sub_list) if isinstance(sub_list, list) else 0}) 非法，必须包含 1 到 16 个数值")
            for val in sub_list:
                _clean_numeric_str(val)

        session.ensure_initialized()
        res = send_client_bt(session.tdx, valid_stock, time_list, series, count=count)
        if as_json:
            click.echo(to_json({"success": True, "action": "bt", **res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"回测数据已推送至客户端: {valid_stock} ({res['count']} 条记录)", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "推送回测数据失败")


@client.command('panel')
@click.option('--input', '-i', 'inputs', multiple=True, required=True,
              help='输入表格文件路径 (.csv, .json, 或 - 从 stdin 读取)，可多次指定')
@click.option('--name', '-n', 'sp_name', required=True, help='自定义面板名/定制版面标识')
@click.option('--layout', type=click.Choice(['vertical', 'horizontal'], case_sensitive=False),
              default='vertical', help='布局方式: vertical (垂直) 或 horizontal (水平)；默认 vertical')
@click.option('--titles', default=None, help='各表格窗口标题，逗号分隔 (数量需等于 inputs 数量)')
@click.option('--heights', default=None, help='各表格窗口高度占比，逗号分隔 (数量需等于 inputs 数量)')
@click.option('--xml-name', default=None, help='自定义生成的 .xml 文件名')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_panel(ctx, inputs: tuple[str, ...], sp_name: str, layout: str,
                 titles: Optional[str], heights: Optional[str],
                 xml_name: Optional[str], as_json: bool):
    """将多个表格数据发布为通达信客户端自定义版面/面板 (.sp, .xml, .jsn)。"""
    try:
        tables: list[pd.DataFrame] = []
        for src in inputs:
            src_str = str(src).strip()
            if src_str == '-':
                content = sys.stdin.read()
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        df = pd.DataFrame(parsed)
                    elif isinstance(parsed, dict):
                        df = pd.DataFrame([parsed])
                    else:
                        raise ValueError()
                except Exception:
                    import io
                    df = pd.read_csv(io.StringIO(content))
            else:
                p = Path(src_str)
                if not p.is_file():
                    raise TdxParamError(f"输入文件不存在: {src_str}")
                ext = p.suffix.lower()
                if ext == '.csv':
                    df = pd.read_csv(p)
                elif ext == '.json':
                    with open(p, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                    if isinstance(data_json, list):
                        df = pd.DataFrame(data_json)
                    elif isinstance(data_json, dict):
                        df = pd.DataFrame([data_json])
                    else:
                        raise TdxParamError(f"无效的 JSON 表格格式: {src_str}")
                else:
                    raise TdxParamError(f"不支持的输入文件格式: {src_str} (仅支持 .csv, .json 或 -)")
            tables.append(df)

        title_list = [t.strip() for t in titles.split(',')] if titles else None
        height_list = [h.strip() for h in heights.split(',')] if heights else None

        res = publish_panel_to_tdx(
            session.tdx,
            config.tdx_path,
            tables,
            sp_name=sp_name,
            xml_filename=xml_name,
            layout=layout,
            heights=height_list,
            titles=title_list,
        )
        payload = {
            "success": True,
            "action": "panel",
            "sp_name": sp_name,
            **res,
        }
        if as_json:
            click.echo(to_json(payload, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"版面 {sp_name} 已成功发布至通达信客户端", fg='green'))
            click.echo(f"  - sp_path: {res.get('sp_path')}")
            click.echo(f"  - xml_path: {res.get('xml_path')}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "发布客户端面板失败")


@client.command('download')
@click.option('--stock', '-s', required=True, help='下载文件关联的股票代码 (例: 688318.SH)')
@click.option('--type', '-t', 'down_type', default=1, type=click.IntRange(min=1, max=7),
              help='下载文件类型 (1=十大股东, 2=ETF申赎数据等；范围 1-7，默认 1)')
@click.option('--date', '-d', 'down_date', help='指定时间 (YYYYMMDD 或 YYYYMMDDHHMMSS，默认当前时间)')
@click.option('--parse', is_flag=True, help='下载完成后自动解析本地数据文件')
@click.option('--parse-only', is_flag=True, help='仅离线解析本地数据文件，不发起网络下载')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_download(ctx, stock: str, down_type: int, down_date: Optional[str],
                    parse: bool, parse_only: bool, as_json: bool):
    """从通达信服务器下载盘后或衍生数据文件 (十大股东、ETF申赎数据等)，支持本地解析。"""
    try:
        stock = stock_code(stock)
        if down_date:
            validate_dates(down_date, None)

        if parse and parse_only:
            raise TdxParamError("--parse 与 --parse-only 互斥，请二选一")

        if parse_only:
            parsed = parse_downloaded_file(config.tdx_path, stock, int(down_type), down_date or '')
            payload = {"success": True, "action": "parse_download", "stock": stock.strip(), "parsed": parsed}
            if as_json:
                click.echo(to_json(payload, ensure_ascii=False, indent=2))
            else:
                click.echo(click.style(f"已成功解析本地文件: {parsed.get('file')}", fg='green'))
                click.echo(to_json(parsed, ensure_ascii=False, indent=2))
            return

        session.ensure_initialized()
        res = session.tdx.download_file(stock_code=stock.strip(), down_time=down_date or '', down_type=int(down_type))
        if isinstance(res, str):
            try:
                res_obj = json.loads(res)
            except ValueError:
                res_obj = {'raw': res}
        else:
            res_obj = res

        payload = {"success": True, "action": "download", "stock": stock.strip(), "result": res_obj}

        if parse:
            parsed = parse_downloaded_file(config.tdx_path, stock, int(down_type), down_date or '')
            payload["parsed"] = parsed

        if as_json:
            click.echo(to_json(payload, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"下载指令已成功发送 -> {stock.strip()}: {res}", fg='green'))
            if parse:
                click.echo(click.style(f"解析结果 -> {payload['parsed'].get('file')}", fg='cyan'))
                click.echo(to_json(payload["parsed"], ensure_ascii=False, indent=2))
    except Exception as e:
        _handle_error(ctx, e, as_json, "下载文件失败")


# ==================== 实盘与模拟交易命令组 ====================

@cli.group()
def trade():
    """交易与账户管理命令 (账户查询、资产、持仓、委托、下单与撤单)"""
    pass


@trade.command('account')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_account(ctx, as_json: bool):
    """查询通达信当前登录的交易账户 ID 与状态"""
    try:
        session.ensure_initialized()
        acc_id = session.tdx.stock_account()
        logged_in = (acc_id is not None and acc_id >= 0)
        res = {
            "account_id": acc_id if logged_in else None,
            "logged_in": logged_in,
            "state": "ready" if logged_in else "unavailable",
            "message": "交易账户已就绪" if logged_in else "交易账户未登录或不存在"
        }
        if as_json:
            click.echo(to_json(res, ensure_ascii=False, indent=2))
        else:
            if logged_in:
                click.echo(click.style(f"当前交易账户 ID: {acc_id} [已登录]", fg='green'))
            else:
                click.echo(click.style("交易账户未登录或不存在，请在客户端核对账户。", fg='yellow'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取账户状态失败")


@trade.command('asset')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID (默认使用当前登录账户)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_asset(ctx, account_id: Optional[int], as_json: bool):
    """查询账户总资产、可用资金与可取资金"""
    try:
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        if account_id is None or account_id < 0:
            raise TdxError("未检测到有效登录的交易账户，请先在通达信客户端登录交易账户")

        asset_data = session.tdx.query_stock_asset(account_id=account_id)
        if as_json:
            click.echo(to_json(asset_data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 账户 {account_id} 资产明细 ===")
            for k, v in asset_data.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "查询资产失败")


@trade.command('positions')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID (默认使用当前登录账户)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_positions(ctx, account_id: Optional[int], as_json: bool):
    """查询股票持仓明细 (股票代码、持仓量、可用量、成本价、盈亏)"""
    try:
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        if account_id is None or account_id < 0:
            raise TdxError("未检测到有效登录的交易账户，请先在通达信客户端登录交易账户")

        pos_list = session.tdx.query_stock_positions(account_id=account_id)
        if as_json:
            click.echo(to_json(pos_list, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 账户 {account_id} 持仓列表 ({len(pos_list)} 只) ===")
            if pos_list:
                df = pd.DataFrame(pos_list)
                click.echo(df.to_string(index=False))
            else:
                click.echo("当前无持仓股票")
    except Exception as e:
        _handle_error(ctx, e, as_json, "查询持仓失败")


@trade.command('orders')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID')
@click.option('--stock', '-s', default='', help='筛选指定股票代码')
@click.option('--cancelable-only', is_flag=True, help='仅查询可撤单委托')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_orders(ctx, account_id: Optional[int], stock: str, cancelable_only: bool, as_json: bool):
    """查询当日委托与成交订单明细"""
    try:
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        if account_id is None or account_id < 0:
            raise TdxError("未检测到有效登录的交易账户，请先在通达信客户端登录交易账户")

        orders = session.tdx.query_stock_orders(
            account_id=account_id,
            stock_code=stock.strip(),
            cancelable_only=cancelable_only
        )
        if as_json:
            click.echo(to_json(orders, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 账户 {account_id} 委托订单 ({len(orders)} 条) ===")
            if orders:
                df = pd.DataFrame(orders)
                click.echo(df.to_string(index=False))
            else:
                click.echo("无委托记录")
    except Exception as e:
        _handle_error(ctx, e, as_json, "查询委托订单失败")


@trade.command('order')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--type', '-t', 'order_type', type=click.Choice(['buy', 'sell']), required=True, help='买卖方向 (buy/sell)')
@click.option('--volume', '-v', required=True, type=int, help='委托数量 (股，如 100)')
@click.option('--price', '-p', type=float, default=0.0, help='委托价格；限价须大于零，市价须指定 --price-type 1')
@click.option('--price-type', default=0, type=int, help='报价方式 (0=限价, 1=市价等)')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID (数值)')
@click.option('--account', default=None, help='字符串资金账号 (适配 v1 SetNewOrder)')
@click.option('--strategy-name', default='tdx-tq-cli', help='策略名称 (默认 tdx-tq-cli)')
@click.option('--remark', default='', help='订单备注')
@click.option('--confirm', is_flag=True, help='确认提交 (未指定则为安全预览模式)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_order(ctx, stock: str, order_type: str, volume: int, price: float,
                price_type: int, account_id: Optional[int], account: Optional[str],
                strategy_name: str, remark: str, confirm: bool, as_json: bool):
    """校验并预览股票委托；--confirm 提交，成功响应代表 SDK 受理。"""
    try:
        if account_id is not None and account is not None:
            raise TdxParamError("--account-id 与 --account 互斥，请二选一")
        if account is not None and not account.strip():
            raise TdxParamError("交易账户账号不能为空")

        effective_account = account.strip() if account is not None else account_id

        request = OrderRequest(stock, order_type, volume, price, price_type)
        request.validate()

        if isinstance(effective_account, int) and effective_account < 0:
            raise TdxParamError("账户 ID 必须为非负整数")

        if not confirm:
            preview = {'status': 'preview', 'warning': '预览未提交；账户状态及市场规则需提交时核实',
                       'order': request.as_dict(effective_account)}
            click.echo(to_json(preview) if as_json else f"[预览] {to_json(preview['order'])}\n追加 --confirm 提交")
            return

        session.ensure_initialized()
        if effective_account is None:
            effective_account = session.tdx.stock_account()

        result = submit_order(session.tdx, request, effective_account,
                              strategy_name=strategy_name, order_remark=remark)
        click.echo(to_json({'success': True, 'status': 'accepted', 'order_result': result})
                   if as_json else f"SDK 已受理委托: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "下单失败")


@trade.command('cancel')
@click.option('--order-id', '-o', required=True, help='待撤订单号')
@click.option('--stock', '-s', default='', help='对应股票代码')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_cancel(ctx, order_id: str, stock: str, account_id: Optional[int], as_json: bool):
    """撤销指定委托；SDK 受理后仍应查询最终状态。"""
    try:
        if not order_id.strip():
            raise TdxParamError("订单号不得为空")
        if stock:
            stock_code(stock)
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        result = cancel_order(session.tdx, account_id, order_id, stock)
        click.echo(to_json({'success': True, 'status': 'accepted', 'cancel_result': result})
                   if as_json else f"SDK 已受理撤单: {result}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "撤单失败")


# ==================== 配置与状态命令组 ====================

@cli.group('config')
def config_cmd():
    """配置管理命令"""
    pass


@config_cmd.command('show')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def config_show(ctx, as_json: bool):
    """显示配置来源、路径和文件错误，不加载 SDK。"""
    data = ctx.obj.get('config', config).get_status()
    click.echo(to_json(data) if as_json else format_table(data))


@config_cmd.command('set')
@click.option('--tdx-path', required=True, help='通达信安装路径 (如 D:\\tdx)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def config_set(ctx, tdx_path: str, as_json: bool):
    """设置并原子保存通达信路径。"""
    try:
        config.tdx_path = tdx_path
        saved = config.save()
        data = {'success': True, 'tdx_path': config.tdx_path, 'config_file': str(saved)}
        click.echo(to_json(data) if as_json else f"通达信路径已保存: {tdx_path} ({saved})")
    except Exception as e:
        _handle_error(ctx, e, as_json, "保存配置失败")


@config_cmd.command('detect')
@click.option('--apply', is_flag=True, help='将探测到的推荐路径自动保存至配置文件')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def config_detect(ctx, apply: bool, as_json: bool):
    """自动探测通达信 TQ 安装路径及运行中客户端。"""
    try:
        discovered = config.discover_tdx_paths()
        running_clients = detect_running_tdx_clients()

        running_resolved = set()
        for p in running_clients:
            try:
                running_resolved.add(Path(p).resolve())
            except Exception:
                pass

        recommended_path = None
        for item in discovered:
            cand = item.get('path') if isinstance(item, dict) else item
            try:
                if Path(cand).resolve() in running_resolved:
                    recommended_path = cand
                    break
            except Exception:
                pass

        if recommended_path is None and discovered:
            first = discovered[0]
            recommended_path = first.get('path') if isinstance(first, dict) else first

        saved_file = None
        if apply:
            if recommended_path is None:
                raise TdxConfigError("未自动探测到可用的通达信 TQ 安装路径，请使用 config set --tdx-path 手动指定")
            config.tdx_path = recommended_path
            saved_file = str(config.save())

        data = {
            'success': True,
            'running_clients': running_clients,
            'discovered': discovered,
            'recommended_path': recommended_path,
            'applied': bool(apply),
        }
        if saved_file:
            data['config_file'] = saved_file
            data['tdx_path'] = config.tdx_path

        if as_json:
            click.echo(to_json(data))
        else:
            if apply:
                click.echo(f"已自动探测并保存通达信路径: {recommended_path} ({saved_file})")
            else:
                click.echo("=== 自动探测通达信安装路径 ===")
                click.echo(f"运行中客户端: {running_clients if running_clients else '未检测到运行中 tdxw.exe'}")
                click.echo(f"发现可用路径: {len(discovered)} 个")
                for item in discovered:
                    click.echo(f"  - {item['path']} ({item['plugin_type']})")
                click.echo(f"推荐路径: {recommended_path or '无'} (追加 --apply 保存配置)")
    except Exception as e:
        _handle_error(ctx, e, as_json, "探测通达信路径失败")


@cli.command()
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def status(ctx, as_json: bool):
    """诊断当前进程状态和 SDK 来源，不建立连接。"""
    running_clients = detect_running_tdx_clients()
    try:
        resolved_tdx = Path(config.tdx_path).resolve()
        path_mismatch = bool(running_clients and resolved_tdx not in {Path(p).resolve() for p in running_clients})
    except Exception:
        path_mismatch = False

    result = {
        'session_initialized': session.is_initialized,
        'session_state': getattr(session, 'state', 'uninitialized'),
        'scope': 'process',
        'config': config.get_status(),
        'daemon': get_daemon_status(config, timeout=0.5),
        'sdk': session.diagnostics(),
        'running_clients': running_clients,
        'path_mismatch': path_mismatch,
    }
    click.echo(to_json(result) if as_json else format_table(result))


def main():
    """CLI 入口点。"""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    cli(obj={})


if __name__ == '__main__':
    main()
