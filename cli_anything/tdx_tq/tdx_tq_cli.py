#!/usr/bin/env python
"""
TDX TQ SDK CLI - 通达信量化接口命令行工具

基于通达信 TQ 策略核心 (D:\\tdx\\PYPlugins\\sys\\tqcenter.py)，
提供完整的行情获取、股票信息、板块分析、财务数据及公式管理命令行工具。
"""

import sys
import os
import json
import click
import pandas as pd
from typing import Optional, List, Dict, Any
from pathlib import Path

from .core.session import session, TdxError
from .core.errors import TdxDataError, TdxParamError
from .core.market import (reformat_kline_data as _reformat_kline_data, normalize_period,
                          normalize_refresh_period, stock_code, validate_dates, MarketRequest)
from .core.quality import parse_fields, read_batch, assess_market, assess_financial
from .core.trading import OrderRequest, submit_order, cancel_order
from .core.sdk import validate_result
from .utils.cli import JsonGroup, configure_debug, handle_error as _handle_error
from . import __version__
from .core.config import config
from .core.export import export_dataframe, export_dict
from .utils.output import format_output, to_json, format_table


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
@click.pass_context
def cli(ctx, tdx_path: Optional[str], debug: bool):
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


# ==================== 行情数据命令组 ====================

@cli.group()
def market():
    """行情数据相关命令 (K线、实时快照、除权除息、涨跌停、量价)"""
    pass


@market.command('get')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔或管道输入')
@click.option('--period', '-p', default='1d', help='周期 (60m=1h，默认 1d)')
@click.option('--count', '-c', default=None, type=click.IntRange(min=1), help='尾部数量 (未指定 --start 时默认 60)')
@click.option('--dividend', '-d', type=click.Choice(['none', 'front', 'back']), default='front', help='复权类型 (默认 front)')
@click.option('--start', help='区间开始时间 (YYYYMMDD 或 YYYYMMDDHHMMSS)，与 --count 互斥')
@click.option('--end', help='结束时间 (YYYYMMDD 包含整日，或 YYYYMMDDHHMMSS)')
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--output', '-o', help='输出文件路径 (.csv, .json, .xlsx)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_get(ctx, stocks: str, period: str, count: Optional[int], dividend: str,
               start: Optional[str], end: Optional[str], allow_partial: bool,
               output: Optional[str], as_json: bool):
    """获取 K 线；指定 --start 读取日期区间，否则读取尾部数量。"""
    try:
        stock_list = _parse_stock_list(stocks)
        request = MarketRequest(period, count, start, end)
        fmt = None
        if output:
            fmt = {'.csv': 'csv', '.json': 'json', '.xlsx': 'excel'}.get(Path(output).suffix.lower())
            if fmt is None:
                raise TdxParamError("输出文件扩展名必须为 .csv、.json 或 .xlsx")
        session.ensure_initialized()
        data, metadata = read_batch(session.tdx, 'get_market_data', stock_list=stock_list,
                                    dividend_type=dividend, **request.sdk_kwargs())
        reformatted = _reformat_kline_data(data)
        request.validate_output(reformatted)
        batch = assess_market(reformatted, stock_list, metadata).require(allow_partial)
        exported = {}
        if output:
            rows = [dict(bar, stock=code) for code, bars in batch.data.items() for bar in bars]
            export_dataframe(pd.DataFrame(rows), output, format=fmt)
            exported = {'output': output, 'format': fmt, 'records': len(rows)}
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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_snapshot(ctx, stock: str, as_json: bool):
    """获取实时五档行情快照 (支持多代码与管道输入)"""
    try:
        session.ensure_initialized()
        stk_list = _parse_stock_list(stock)
        if not stk_list:
            raise ValueError("未指定有效的股票代码")

        if len(stk_list) == 1:
            snapshot = validate_result(session.tdx.get_market_snapshot(stk_list[0]), 'get_market_snapshot', require_data=True)
            if as_json:
                click.echo(to_json(snapshot, ensure_ascii=False, indent=2))
            else:
                click.echo(f"=== {stk_list[0]} 实时快照 ===")
                for k, v in snapshot.items():
                    click.echo(f"{k}: {v}")
        else:
            snapshots = {}
            for s in stk_list:
                snapshots[s] = validate_result(session.tdx.get_market_snapshot(s), 'get_market_snapshot', require_data=True)
            if as_json:
                click.echo(to_json(snapshots, ensure_ascii=False, indent=2))
            else:
                for s, snap in snapshots.items():
                    click.echo(f"\n=== {s} 实时快照 ===")
                    for k, v in snap.items():
                        click.echo(f"{k}: {v}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取行情快照失败")


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
    """获取指定股票的涨跌停状态与封板明细；不生成全市场连板天梯。"""
    try:
        session.ensure_initialized()
        stock_list = _parse_stock_list(stocks)
        res = session.tdx.get_zdt_data(stock_list=stock_list)

        if as_json:
            click.echo(to_json(res, ensure_ascii=False, indent=2))
        else:
            click.echo("=== 涨跌停数据 ===")
            for code, data in res.items():
                click.echo(f"{code}: {data}")
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
@click.option('--market', '-m', default='AG', help='市场标识 (AG=A股, HK=港股, US=美股, QH=期货, ZS=指数)；指定 --stocks 时不生效')
@click.option('--force', is_flag=True, help='是否强制刷新全部缓存')
@click.option('--stocks', '-s', help='刷新指定股票的历史K线缓存，逗号分隔')
@click.option('--period', '-p', default='1d', help='K线周期 (仅 1m, 5m, 1d；刷新接口只支持这三个周期)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_refresh(ctx, market: str, force: bool, stocks: Optional[str], period: str, as_json: bool):
    """刷新客户端行情缓存。"""
    try:
        stock_list = _parse_stock_list(stocks) if stocks else None
        if stocks:
            period = normalize_refresh_period(period)
        elif normalize_period(period) != '1d':
            raise TdxParamError("--period 仅在指定 --stocks 刷新历史K线缓存时生效")
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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_info(ctx, stock: str, as_json: bool):
    """获取股票基础财务与属性信息"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        info = session.tdx.get_stock_info(stock.strip())

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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_list(ctx, market: str, list_type: int, as_json: bool):
    """获取指定股票集合；默认沪深300，A股集合需指定 -m 5。"""
    try:
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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_search(ctx, keyword: str, as_json: bool):
    """按名称或代码模糊搜索匹配股票"""
    try:
        session.ensure_initialized()
        results = session.tdx.get_match_stkinfo(key_word=keyword.strip())

        if as_json:
            click.echo(to_json(results, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 搜索 '{keyword}' 匹配结果 ({len(results)} 条) ===")
            for item in results:
                click.echo(f"{item.get('Code')}: {item.get('Name')}")
    except Exception as e:
        _handle_error(ctx, e, as_json, "搜索股票失败")


@stock.command('gb')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--count', '-c', default=5, type=click.IntRange(min=1), help='获取记录数 (默认 5)')
@click.option('--dates', '-d', help='指定交易日期列表，逗号分隔 (如 20261020,20261019)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_gb(ctx, stock: str, count: int, dates: Optional[str], as_json: bool):
    """查询真实日期的股本数据，日期不足时不重复补齐。"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        if dates:
            date_list = list(dict.fromkeys(d.strip() for d in dates.split(',') if d.strip()))
            if not date_list:
                raise TdxParamError("日期列表不能为空")
            for date in date_list:
                validate_dates(date, None)
        else:
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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_kzz(ctx, stock: str, as_json: bool):
    """获取可转债基本属性信息"""
    try:
        stock = stock_code(stock)
        session.ensure_initialized()
        data = session.tdx.get_kzz_info(stock_code=stock.strip())

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


# ==================== 板块数据命令组 ====================

@cli.group()
def sector():
    """板块数据相关命令 (板块列表、成分股、自定义板块管理)"""
    pass


@sector.command('list')
@click.option('--type', '-t', 'list_type', type=click.Choice(['0', '1', '2']), default='0',
              help='板块类型 (0=行业，1=概念，2=风格)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_list(ctx, list_type: str, as_json: bool):
    """获取系统板块列表"""
    try:
        session.ensure_initialized()
        sectors = session.tdx.get_sector_list(list_type=int(list_type))

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
@click.option('--sector', '-s', required=True,
              help='板块代码、板块名称或自定义板块简称 (例: 880201.SH / 半导体 / ZXG)')
@click.option('--block-type', '-b', type=click.Choice(['0', '1']), default='0',
              help='板块类型 (0=板块指数代码或名称，1=自定义板块简称，如 ZXG=自选股、TJG=临时条件股)')
@click.option('--type', '-t', 'list_type', default=0, type=int,
              help='返回内容 (0=仅代码，1=代码+名称)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_stocks(ctx, sector: str, block_type: str, list_type: int, as_json: bool):
    """获取指定板块的成分股列表。"""
    try:
        block = sector.strip()
        if not block:
            raise TdxParamError("板块代码或名称不得为空")
        session.ensure_initialized()
        stocks = session.tdx.get_stock_list_in_sector(
            block_code=block, block_type=int(block_type), list_type=list_type)

        if as_json:
            click.echo(to_json(stocks, ensure_ascii=False, indent=2))
        else:
            label = '自定义板块' if int(block_type) == 1 else '板块'
            click.echo(f"=== {label}: {block} (成分股 {len(stocks)} 只) ===")
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
@click.option('--code', '-c', required=True, help='自定义板块代码 (如 ZXG 或 自定义代码)')
@click.option('--stocks', '-s', required=True, help='股票列表，逗号分隔 (例: 600519.SH,000001.SZ)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_update_stocks(ctx, code: str, stocks: str, as_json: bool):
    """更新自定义板块成分股。"""
    try:
        if not code.strip():
            raise TdxParamError("板块代码不得为空")
        codes = _parse_stock_list(stocks)
        session.ensure_initialized()
        result = session.tdx.send_user_block(block_code=code.strip(), stock_list=codes, show=True)
        click.echo(to_json({'success': True, 'result': result, 'count': len(codes)})
                   if as_json else f"板块已更新: {result}")
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
@click.option('--allow-partial', is_flag=True, help='允许部分可用数据，返回带完整性说明的 JSON 信封')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_get(ctx, stocks: str, fields: str, start: Optional[str], end: Optional[str],
                  allow_partial: bool, as_json: bool):
    """获取专业财务报表历史数据，并检查请求字段完整性。"""
    try:
        stock_list = _parse_stock_list(stocks)
        field_list = parse_fields(fields)
        validate_dates(start, end)
        session.ensure_initialized()
        data, metadata = read_batch(session.tdx, 'get_financial_data', stock_list=stock_list,
                                    field_list=field_list, start_time=start or '', end_time=end or '')
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
@click.option('--fields', '-f', required=True, help='统计指标字段，逗号分隔 (如 GP1,GP2 或 SC1,SC2)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_trading(ctx, target_type: str, target: str, fields: str,
                      start: Optional[str], end: Optional[str], as_json: bool):
    """获取股票/板块/全市场交易统计特征数据"""
    try:
        session.ensure_initialized()
        field_list = [f.strip() for f in fields.split(',') if f.strip()]

        if target_type == 'stock':
            stock_list = [s.strip() for s in target.split(',') if s.strip()]
            res = session.tdx.get_gpjy_value(
                stock_list=stock_list,
                field_list=field_list,
                start_time=start or '',
                end_time=end or ''
            )
        elif target_type == 'sector':
            sector_list = [s.strip() for s in target.split(',') if s.strip()]
            res = session.tdx.get_bkjy_value(
                stock_list=sector_list,
                field_list=field_list,
                start_time=start or '',
                end_time=end or ''
            )
        else:
            res = session.tdx.get_scjy_value(
                field_list=field_list,
                start_time=start or '',
                end_time=end or ''
            )

        if as_json:
            click.echo(to_json(res, ensure_ascii=False, indent=2))
        else:
            click.echo(res)
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取交易统计数据失败")


# ==================== 公式调用与管理命令组 ====================

@cli.group()
def formula():
    """通达信公式系统命令 (指标公式、选股公式、公式浏览与详情)"""
    pass


@formula.command('list')
@click.option('--type', '-t', 'formula_type', type=click.Choice(['0', '1', '2']), default='0',
              help='公式类型 (0=指标公式，1=选股公式，2=专家公式)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_list(ctx, formula_type: str, as_json: bool):
    """获取所有可用通达信指标或选股公式列表"""
    try:
        session.ensure_initialized()
        all_formulas = session.tdx.formula_get_all(formula_type=int(formula_type))

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
@click.option('--type', '-t', 'formula_type', default=0, type=int, help='公式类型 (0=指标, 1=选股)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_info(ctx, code: str, formula_type: int, as_json: bool):
    """获取指定公式的详细定义与参数说明"""
    try:
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
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_zb(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int, as_json: bool):
    """调用通达信指标公式并计算结果"""
    try:
        period = normalize_period(period)
        session.ensure_initialized()
        if stocks:
            stock_list = _parse_stock_list(stocks)
            result = session.tdx.formula_process_mul_zb(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                stock_list=stock_list,
                stock_period=period,
                count=count
            )
        else:
            result = session.tdx.formula_zb(
                formula_name=name.strip(),
                formula_arg=args.strip()
            )

        if as_json:
            click.echo(to_json(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 指标公式: {name} 计算结果 ===")
            click.echo(result)
    except Exception as e:
        _handle_error(ctx, e, as_json, "调用指标公式失败")


@formula.command('xg')
@click.option('--name', '-n', required=True, help='选股公式名称 (如 UPN, KDJ)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔 (如 3)')
@click.option('--stocks', '-s', help='选股候选股票列表，逗号分隔')
@click.option('--period', '-p', default='1d', help='周期 (1d 等)')
@click.option('--count', '-c', default=60, type=click.IntRange(min=1), help='K线根数 (默认 60)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_xg(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int, as_json: bool):
    """调用通达信选股公式筛选股票"""
    try:
        period = normalize_period(period)
        session.ensure_initialized()
        if stocks:
            stock_list = _parse_stock_list(stocks)
            result = session.tdx.formula_process_mul_xg(
                formula_name=name.strip(),
                formula_arg=args.strip(),
                stock_list=stock_list,
                stock_period=period,
                count=count
            )
        else:
            result = session.tdx.formula_xg(
                formula_name=name.strip(),
                formula_arg=args.strip()
            )

        if as_json:
            click.echo(to_json(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 选股公式: {name} 筛选结果 ===")
            click.echo(result)
    except Exception as e:
        _handle_error(ctx, e, as_json, "调用选股公式失败")


# ==================== 客户端联动与预警命令组 ====================

@cli.group()
def client():
    """通达信桌面端 GUI 联动与预警通知 (联动看盘、预警弹窗、消息推送)"""
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
@click.option('--stock', '-s', required=True, help='预警股票代码 (例: 600519.SH)')
@click.option('--message', '-m', required=True, help='预警提示内容')
@click.option('--price', '-p', default='0.0', help='当前触发价格')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def client_warn(ctx, stock: str, message: str, price: str, as_json: bool):
    """向通达信桌面端发送右下角交易预警弹窗"""
    try:
        stock = stock_code(stock)
        import datetime
        session.ensure_initialized()
        now_time = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        stk_clean = stock.strip()
        res = session.tdx.send_warn(
            stock_list=[stk_clean],
            time_list=[now_time],
            price_list=[str(price)],
            close_list=[str(price)],
            volum_list=['0'],
            bs_flag_list=['0'],
            warn_type_list=['1'],
            reason_list=[message.strip()],
            count=1
        )
        if as_json:
            click.echo(to_json({"success": True, "action": "warn", "stock": stk_clean, "raw": res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"预警已发送至客户端 -> {stk_clean}: {message}", fg='green'))
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
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID')
@click.option('--confirm', is_flag=True, help='确认提交 (未指定则为安全预览模式)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_order(ctx, stock: str, order_type: str, volume: int, price: float,
                price_type: int, account_id: Optional[int], confirm: bool, as_json: bool):
    """校验并预览股票委托；--confirm 提交，成功响应代表 SDK 受理。"""
    try:
        request = OrderRequest(stock, order_type, volume, price, price_type)
        request.validate()
        if account_id is not None and account_id < 0:
            raise TdxParamError("账户 ID 必须为非负整数")
        if not confirm:
            preview = {'status': 'preview', 'warning': '预览未提交；账户状态及市场规则需提交时核实',
                       'order': request.as_dict(account_id)}
            click.echo(to_json(preview) if as_json else f"[预览] {to_json(preview['order'])}\n追加 --confirm 提交")
            return
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        result = submit_order(session.tdx, request, account_id)
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


@cli.command()
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def status(ctx, as_json: bool):
    """诊断当前进程状态和 SDK 来源，不建立连接。"""
    result = {'session_initialized': session.is_initialized, 'scope': 'process',
              'config': config.get_status(), 'sdk': session.diagnostics()}
    click.echo(to_json(result) if as_json else format_table(result))


def main():
    """CLI 入口点。"""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    cli(obj={})


if __name__ == '__main__':
    main()
