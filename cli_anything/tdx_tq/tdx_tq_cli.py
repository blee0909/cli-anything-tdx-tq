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

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加当前目录与根目录
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).parent))

from .core.session import session, TdxError
from .core.config import config
from .core.export import export_dataframe, export_dict
from .utils.output import format_output, to_json, format_table


def _reformat_kline_data(raw_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    将 tq.get_market_data 返回的多指标 DataFrame 字典重构为标准的按股票组织的 OHLCV 记录。
    确保保留并规范化 Date/Time 交易日期。
    """
    stocks = []
    dates = []
    field_dfs = {}

    for f_name, df in raw_data.items():
        if isinstance(df, pd.DataFrame):
            field_dfs[f_name.lower()] = df
            if not stocks:
                stocks = list(df.columns)
            if not dates:
                dates = list(df.index)

    result = {}
    for stock in stocks:
        stock_bars = []
        for d in dates:
            # 格式化日期字符串
            date_str = str(d)[:10] if hasattr(d, 'strftime') else str(d)
            bar: Dict[str, Any] = {'date': date_str}
            for f_name, df in field_dfs.items():
                if stock in df.columns and d in df.index:
                    val = df.loc[d, stock]
                    bar[f_name] = float(val) if pd.notnull(val) else None
            stock_bars.append(bar)
        result[stock] = stock_bars

    return result


def _handle_error(ctx, err: Exception, as_json: bool = False, prefix: str = "操作失败"):
    """统一的错误处理，支持人类可读与结构化 JSON 输出"""
    if as_json:
        err_obj = {
            "success": False,
            "error": str(err),
            "error_type": type(err).__name__
        }
        click.echo(json.dumps(err_obj, ensure_ascii=False, indent=2))
    else:
        click.echo(click.style(f"{prefix}：{err}", fg='red'), err=True)
    ctx.exit(1)


def _parse_stock_list(stocks_input: str) -> List[str]:
    """解析股票代码列表，支持逗号分隔、空格分隔或从标准输入管道读取 ('-')"""
    if stocks_input.strip() == '-':
        raw = sys.stdin.read()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except Exception:
            pass
        tokens = raw.replace('\n', ',').replace(' ', ',').split(',')
        return [t.strip() for t in tokens if t.strip()]
    return [s.strip() for s in stocks_input.split(',') if s.strip()]


@click.group()
@click.version_option(version='1.1.0', prog_name='cli-anything-tdx-tq')
@click.option('--tdx-path', envvar='TDX_PATH', help='通达信安装路径 (默认 D:\\tdx)')
@click.pass_context
def cli(ctx, tdx_path: Optional[str]):
    """
    TDX TQ SDK CLI - 通达信量化接口命令行工具

    基于 TQ 策略模式，提供行情、股票、板块、财务与公式的全功能命令行接口。

    前置条件:
    1. 通达信客户端已启动并登录
    2. 通达信安装在 D:\\tdx 或通过 --tdx-path 指定
    """
    ctx.ensure_object(dict)
    if tdx_path:
        config.tdx_path = tdx_path
    ctx.obj['config'] = config


@cli.command()
@click.pass_context
def init(ctx):
    """初始化通达信连接"""
    try:
        session.initialize()
        click.echo(click.style("连接已建立 (run_id: %d)" % getattr(session.tdx, 'run_id', -1), fg='green'))
    except Exception as e:
        click.echo(click.style(f"初始化失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@cli.command()
@click.pass_context
def close(ctx):
    """关闭通达信连接"""
    try:
        session.close()
        click.echo(click.style("连接已关闭", fg='green'))
    except Exception as e:
        click.echo(click.style(f"关闭失败：{e}", fg='red'), err=True)
        ctx.exit(1)


# ==================== 行情数据命令组 ====================

@cli.group()
def market():
    """行情数据相关命令 (K线、实时快照、除权除息、涨跌停、量价)"""
    pass


@market.command('get')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔或管道输入 (例: 600519.SH,000001.SZ 或 -)')
@click.option('--period', '-p', default='1d', help='周期 (tick,1m,5m,15m,30m,60m,1d,1w,1mon)')
@click.option('--count', '-c', default=60, type=int, help='获取数量 (默认 60)')
@click.option('--dividend', '-d', type=click.Choice(['none', 'front', 'back']), default='front', help='复权类型 (默认 front)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--output', '-o', help='输出文件路径 (.csv, .json, .xlsx)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_get(ctx, stocks: str, period: str, count: int, dividend: str,
               start: Optional[str], end: Optional[str], output: Optional[str], as_json: bool):
    """获取 K 线历史数据 (完整保留交易日期与 OHLCV 指标，支持管道输入)"""
    try:
        session.ensure_initialized()
        stock_list = _parse_stock_list(stocks)
        if not stock_list:
            raise ValueError("未指定有效的股票代码")
        data = session.tdx.get_market_data(
            stock_list=stock_list,
            period=period,
            count=count,
            dividend_type=dividend,
            start_time=start or '',
            end_time=end or ''
        )

        reformatted = _reformat_kline_data(data)

        if output:
            all_records = []
            for code, bars in reformatted.items():
                for b in bars:
                    rec = dict(b)
                    rec['stock'] = code
                    all_records.append(rec)
            df_out = pd.DataFrame(all_records)
            ext = Path(output).suffix.lower()
            fmt = 'json' if ext == '.json' else 'excel' if ext in ['.xlsx', '.xls'] else 'csv'
            export_dataframe(df_out, output, format=fmt)
            click.echo(click.style(f"数据已成功导出至 {output}", fg='green'))
        elif as_json:
            click.echo(json.dumps(reformatted, ensure_ascii=False, indent=2))
        else:
            for code, bars in reformatted.items():
                click.echo(f"\n=== 股票: {code} ({len(bars)} 条记录) ===")
                if bars:
                    df_view = pd.DataFrame(bars)
                    click.echo(df_view.to_string(index=False))
                else:
                    click.echo("暂无数据")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取 K 线数据失败")


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
            snapshot = session.tdx.get_market_snapshot(stk_list[0])
            if as_json:
                click.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
            else:
                click.echo(f"=== {stk_list[0]} 实时快照 ===")
                for k, v in snapshot.items():
                    click.echo(f"{k}: {v}")
        else:
            snapshots = {}
            for s in stk_list:
                snapshots[s] = session.tdx.get_market_snapshot(s)
            if as_json:
                click.echo(json.dumps(snapshots, ensure_ascii=False, indent=2))
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
        session.ensure_initialized()
        data = session.tdx.get_divid_factors(
            stock_code=stock.strip(),
            start_time=start or '',
            end_time=end or ''
        )

        if as_json:
            records = data.to_dict('records') if isinstance(data, pd.DataFrame) else data
            click.echo(json.dumps(records, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 除权除息数据 ===")
            click.echo(data.to_string() if isinstance(data, pd.DataFrame) else data)
    except Exception as e:
        click.echo(click.style(f"获取除权除息数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@market.command('zdt')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_zdt(ctx, stocks: str, as_json: bool):
    """获取股票涨跌停价格数据"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
        res = session.tdx.get_zdt_data(stock_list=stock_list)

        if as_json:
            click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            click.echo("=== 涨跌停数据 ===")
            for code, data in res.items():
                click.echo(f"{code}: {data}")
    except Exception as e:
        click.echo(click.style(f"获取涨跌停数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@market.command('exday')
@click.option('--stock', '-s', required=True, help='股票代码')
@click.option('--count', '-c', default=10, type=int, help='获取数量 (默认 10)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_exday(ctx, stock: str, count: int, as_json: bool):
    """获取除权除息日统计明细"""
    try:
        session.ensure_initialized()
        data = session.tdx.get_exday_data(stock_code=stock.strip(), count=count)

        if as_json:
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 除权日明细 ({len(data)} 条) ===")
            for item in data:
                click.echo(item)
    except Exception as e:
        click.echo(click.style(f"获取除权日数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@market.command('pv')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_pv(ctx, stocks: str, as_json: bool):
    """获取量价与买卖档位聚合统计"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
        res = session.tdx.get_pricevol(stock_list=stock_list)

        if as_json:
            click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            click.echo("=== 量价统计 ===")
            click.echo(res)
    except Exception as e:
        click.echo(click.style(f"获取量价统计失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@market.command('refresh')
@click.option('--market', '-m', default='AG', help='市场标识 (AG=A股, HK=港股, US=美股, QH=期货, ZS=指数)')
@click.option('--force', is_flag=True, help='是否强制刷新全部缓存')
@click.option('--stocks', '-s', help='刷新指定股票的历史K线缓存，逗号分隔')
@click.option('--period', '-p', default='1d', help='K线周期 (1m, 5m, 1d)')
@click.pass_context
def market_refresh(ctx, market: str, force: bool, stocks: Optional[str], period: str):
    """刷新通达信行情和 K 线缓存"""
    try:
        session.ensure_initialized()
        if stocks:
            stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
            res = session.tdx.refresh_kline(stock_list=stock_list, period=period)
            click.echo(click.style(f"K线缓存已刷新: {res}", fg='green'))
        else:
            res = session.tdx.refresh_cache(market=market, force=force)
            click.echo(click.style(f"行情缓存已刷新: {res}", fg='green'))
    except Exception as e:
        click.echo(click.style(f"刷新缓存失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
        session.ensure_initialized()
        info = session.tdx.get_stock_info(stock.strip())

        if as_json:
            click.echo(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 基础信息 ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(click.style(f"获取股票信息失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@stock.command('more')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--fields', '-f', help='指定字段，逗号分隔')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_more(ctx, stock: str, fields: Optional[str], as_json: bool):
    """获取股票进阶信息（主营业务、形态值、安全分等）"""
    try:
        session.ensure_initialized()
        field_list = [f.strip() for f in fields.split(',')] if fields else []
        info = session.tdx.get_more_info(stock.strip(), field_list=field_list)

        if as_json:
            click.echo(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 进阶信息 ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(click.style(f"获取进阶信息失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@stock.command('list')
@click.option('--market', '-m', default='23', help='市场代码 (23=A股全市场/沪深300, 0=上证, 1=深证)')
@click.option('--type', '-t', 'list_type', default=0, type=int, help='类型 (0=全部, 1=代码列表)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_list(ctx, market: str, list_type: int, as_json: bool):
    """获取全市场股票代码列表"""
    try:
        session.ensure_initialized()
        stocks = session.tdx.get_stock_list(market=market, list_type=list_type)

        if as_json:
            click.echo(json.dumps(stocks, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 股票代码列表 (共 {len(stocks)} 只) ===")
            for s in stocks[:50]:
                click.echo(s)
            if len(stocks) > 50:
                click.echo(f"... 剩余 {len(stocks) - 50} 只省略")
    except Exception as e:
        click.echo(click.style(f"获取股票列表失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@stock.command('calendar')
@click.option('--market', '-m', default='SH', help='市场标识 (SH, SZ, AG)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--count', '-c', default=20, type=int, help='获取交易日数量 (默认 20)')
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
            click.echo(json.dumps(dates, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 交易日历 ({len(dates)} 天) ===")
            for d in dates:
                click.echo(d)
    except Exception as e:
        click.echo(click.style(f"获取交易日历失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
            click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 搜索 '{keyword}' 匹配结果 ({len(results)} 条) ===")
            for item in results:
                click.echo(f"{item.get('Code')}: {item.get('Name')}")
    except Exception as e:
        click.echo(click.style(f"搜索股票失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@stock.command('gb')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--count', '-c', default=5, type=int, help='获取记录数 (默认 5)')
@click.option('--dates', '-d', help='指定交易日期列表，逗号分隔 (如 20261020,20261019)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_gb(ctx, stock: str, count: int, dates: Optional[str], as_json: bool):
    """获取股本变动历史信息 (自动匹配有效交易日，支持自定义日期)"""
    try:
        session.ensure_initialized()
        if dates:
            date_list = [d.strip() for d in dates.split(',') if d.strip()]
        else:
            date_list = session.get_recent_trading_dates(count=count)

        # 确保 date_list 长度至少为 count
        while len(date_list) < count:
            date_list.append(date_list[-1] if date_list else '20260101')

        data = session.tdx.get_gb_info(stock_code=stock.strip(), date_list=date_list[:count], count=count)

        if as_json:
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 股本变动历史 ({len(data)} 条) ===")
            if data:
                df = pd.DataFrame(data)
                click.echo(df.to_string(index=False))
            else:
                click.echo("暂无股本信息")
    except Exception as e:
        _handle_error(ctx, e, as_json, "获取股本信息失败")


@stock.command('kzz')
@click.option('--stock', '-s', required=True, help='可转债代码 (例: 113050.SH)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_kzz(ctx, stock: str, as_json: bool):
    """获取可转债基本属性信息"""
    try:
        session.ensure_initialized()
        data = session.tdx.get_kzz_info(stock_code=stock.strip())

        if as_json:
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
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
            click.echo(json.dumps(sectors, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 板块列表 (共 {len(sectors)} 个) ===")
            for i, sec in enumerate(sectors[:100], 1):
                click.echo(f"{i}. {sec}")
            if len(sectors) > 100:
                click.echo(f"... 剩余 {len(sectors) - 100} 个省略")
    except Exception as e:
        click.echo(click.style(f"获取板块列表失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@sector.command('stocks')
@click.option('--sector', '-s', required=True, help='板块代码或名称 (例: 880201.SH 或 半导体)')
@click.option('--type', '-t', 'list_type', default=0, type=int, help='板块分类')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_stocks(ctx, sector: str, list_type: int, as_json: bool):
    """获取指定板块的成分股列表"""
    try:
        session.ensure_initialized()
        stocks = session.tdx.get_stock_list_in_sector(block_code=sector.strip(), list_type=list_type)

        if as_json:
            click.echo(json.dumps(stocks, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 板块: {sector} (成分股 {len(stocks)} 只) ===")
            for i, stk in enumerate(stocks, 1):
                click.echo(f"{i}. {stk}")
    except Exception as e:
        click.echo(click.style(f"获取成分股失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@sector.command('user')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def sector_user(ctx, as_json: bool):
    """获取当前用户的自选/自定义板块列表"""
    try:
        session.ensure_initialized()
        user_sectors = session.tdx.get_user_sector()

        if as_json:
            click.echo(json.dumps(user_sectors, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 自选/自定义板块 ({len(user_sectors)} 个) ===")
            for sec in user_sectors:
                click.echo(sec)
    except Exception as e:
        click.echo(click.style(f"获取自定义板块失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@sector.command('create')
@click.option('--code', '-c', required=True, help='自定义板块英文/字母代码 (如 MYBK)')
@click.option('--name', '-n', required=True, help='自定义板块中文名称 (如 我的自选)')
@click.pass_context
def sector_create(ctx, code: str, name: str):
    """创建新的自定义板块"""
    try:
        session.ensure_initialized()
        res = session.tdx.create_sector(block_code=code.strip(), block_name=name.strip())
        click.echo(click.style(f"创建自定义板块成功: {res}", fg='green'))
    except Exception as e:
        click.echo(click.style(f"创建板块失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@sector.command('delete')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.pass_context
def sector_delete(ctx, code: str):
    """删除指定的自定义板块"""
    try:
        session.ensure_initialized()
        res = session.tdx.delete_sector(block_code=code.strip())
        click.echo(click.style(f"删除板块成功: {res}", fg='green'))
    except Exception as e:
        click.echo(click.style(f"删除板块失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@sector.command('update-stocks')
@click.option('--code', '-c', required=True, help='自定义板块代码 (如 ZXG 或 自定义代码)')
@click.option('--stocks', '-s', required=True, help='股票列表，逗号分隔 (例: 600519.SH,000001.SZ)')
@click.pass_context
def sector_update_stocks(ctx, code: str, stocks: str):
    """向自定义板块更新/写入成分股"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
        res = session.tdx.send_user_block(block_code=code.strip(), stock_list=stock_list, show=True)
        click.echo(click.style(f"板块成分股已更新 ({len(stock_list)} 只): {res}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, False, "更新板块成分股失败")


@sector.command('rename')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.option('--name', '-n', required=True, help='自定义板块新名称')
@click.pass_context
def sector_rename(ctx, code: str, name: str):
    """重命名自定义板块"""
    try:
        session.ensure_initialized()
        res = session.tdx.rename_sector(block_code=code.strip(), block_name=name.strip())
        click.echo(click.style(f"板块重命名成功: {res}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, False, "重命名板块失败")


@sector.command('clear')
@click.option('--code', '-c', required=True, help='自定义板块代码')
@click.pass_context
def sector_clear(ctx, code: str):
    """清空自定义板块内所有成分股"""
    try:
        session.ensure_initialized()
        res = session.tdx.clear_sector(block_code=code.strip())
        click.echo(click.style(f"板块已成功清空: {res}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, False, "清空板块失败")


# ==================== 财务数据命令组 ====================

@cli.group()
def financial():
    """财务与交易统计命令 (专业财务、报告期筛选、交易统计)"""
    pass


@financial.command('get')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--fields', '-f', required=True, help='字段列表，逗号分隔 (如 FN1,FN4,FN6)')
@click.option('--start', help='开始时间 (YYYYMMDD)')
@click.option('--end', help='结束时间 (YYYYMMDD)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_get(ctx, stocks: str, fields: str, start: Optional[str], end: Optional[str], as_json: bool):
    """获取专业财务报表历史数据"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
        field_list = [f.strip() for f in fields.split(',') if f.strip()]

        data = session.tdx.get_financial_data(
            stock_list=stock_list,
            field_list=field_list,
            start_time=start or '',
            end_time=end or ''
        )

        if as_json:
            result = {}
            for code, df in data.items():
                if isinstance(df, pd.DataFrame):
                    result[code] = df.reset_index().to_dict('records')
                else:
                    result[code] = df
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for code, df in data.items():
                click.echo(f"\n=== {code} 财务数据 ===")
                click.echo(df.to_string() if isinstance(df, pd.DataFrame) else df)
    except Exception as e:
        click.echo(click.style(f"获取财务数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@financial.command('by-date')
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔')
@click.option('--fields', '-f', required=True, help='字段列表，逗号分隔 (如 FN1,FN4)')
@click.option('--year', '-y', default=0, type=int, help='年份 (如 2025，0 表示最新)')
@click.option('--mmdd', '-m', default=0, type=int, help='报告期月日 (如 331, 630, 930, 1231)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def financial_by_date(ctx, stocks: str, fields: str, year: int, mmdd: int, as_json: bool):
    """按指定报告期日期获取财务数据"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
        field_list = [f.strip() for f in fields.split(',') if f.strip()]

        data = session.tdx.get_financial_data_by_date(
            stock_list=stock_list,
            field_list=field_list,
            year=year,
            mmdd=mmdd
        )

        if as_json:
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            click.echo(data)
    except Exception as e:
        click.echo(click.style(f"按日期获取财务数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
            click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            click.echo(res)
    except Exception as e:
        click.echo(click.style(f"获取交易统计数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
            click.echo(json.dumps(all_formulas, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 通达信公式列表 ({len(all_formulas)} 个) ===")
            for f in all_formulas[:100]:
                click.echo(f)
            if len(all_formulas) > 100:
                click.echo(f"... 剩余 {len(all_formulas) - 100} 个公式省略")
    except Exception as e:
        click.echo(click.style(f"获取公式列表失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
            click.echo(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 公式详情: {code} ===")
            for k, v in info.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(click.style(f"获取公式详情失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@formula.command('zb')
@click.option('--name', '-n', required=True, help='指标公式名称 (如 MACD, KDJ)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔 (如 12,26,9)')
@click.option('--stocks', '-s', help='计算股票列表，逗号分隔 (例: 600519.SH,000001.SZ)')
@click.option('--period', '-p', default='1d', help='周期 (1d, 5m 等)')
@click.option('--count', '-c', default=60, type=int, help='K线根数 (默认 60)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_zb(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int, as_json: bool):
    """调用通达信指标公式并计算结果"""
    try:
        session.ensure_initialized()
        if stocks:
            stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
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
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== 指标公式: {name} 计算结果 ===")
            click.echo(result)
    except Exception as e:
        click.echo(click.style(f"调用指标公式失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@formula.command('xg')
@click.option('--name', '-n', required=True, help='选股公式名称 (如 UPN, KDJ)')
@click.option('--args', '-a', default='', help='公式参数，逗号分隔 (如 3)')
@click.option('--stocks', '-s', help='选股候选股票列表，逗号分隔')
@click.option('--period', '-p', default='1d', help='周期 (1d 等)')
@click.option('--count', '-c', default=60, type=int, help='K线根数 (默认 60)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def formula_xg(ctx, name: str, args: str, stocks: Optional[str], period: str, count: int, as_json: bool):
    """调用通达信选股公式筛选股票"""
    try:
        session.ensure_initialized()
        if stocks:
            stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
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
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
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
        session.ensure_initialized()
        stk_clean = stock.strip()
        code_pure = stk_clean.split('.')[0] if '.' in stk_clean else stk_clean
        url = f"tdx://stock?code={code_pure}"
        res = session.tdx.exec_to_tdx(url=url)
        if as_json:
            click.echo(json.dumps({"success": True, "action": "link", "stock": stk_clean, "raw": res}, ensure_ascii=False, indent=2))
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
        import datetime
        session.ensure_initialized()
        now_time = datetime.datetime.now().strftime('%H%M%S')
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
            click.echo(json.dumps({"success": True, "action": "warn", "stock": stk_clean, "raw": res}, ensure_ascii=False, indent=2))
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
            click.echo(json.dumps({"success": True, "action": "msg", "message": message, "raw": res}, ensure_ascii=False, indent=2))
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
            "message": "交易账户已就绪" if logged_in else "通达信客户端交易账户未登录"
        }
        if as_json:
            click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if logged_in:
                click.echo(click.style(f"当前交易账户 ID: {acc_id} [已登录]", fg='green'))
            else:
                click.echo(click.style("当前未登录交易账户，请在通达信客户端登录交易账户后操作。", fg='yellow'))
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
            click.echo(json.dumps(asset_data, ensure_ascii=False, indent=2))
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
            click.echo(json.dumps(pos_list, ensure_ascii=False, indent=2))
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
            click.echo(json.dumps(orders, ensure_ascii=False, indent=2))
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
@click.option('--price', '-p', type=float, default=0.0, help='委托价格 (0 表示市价/当前价)')
@click.option('--price-type', default=0, type=int, help='报价方式 (0=限价, 1=市价等)')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID')
@click.option('--confirm', is_flag=True, help='确认提交 (未指定则为安全预览模式)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_order(ctx, stock: str, order_type: str, volume: int, price: float,
                price_type: int, account_id: Optional[int], confirm: bool, as_json: bool):
    """提交股票买卖委托 (默认需 --confirm 确认防止误操作)"""
    try:
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        if account_id is None or account_id < 0:
            raise TdxError("未检测到有效登录的交易账户，请先在通达信客户端登录交易账户")

        if not confirm:
            preview = {
                "status": "preview",
                "warning": "这是安全预览模式，未实际发送交易指令。如需下单请加上 --confirm 参数",
                "order": {
                    "account_id": account_id,
                    "stock": stock.strip(),
                    "direction": order_type,
                    "volume": volume,
                    "price": price,
                    "price_type": price_type
                }
            }
            if as_json:
                click.echo(json.dumps(preview, ensure_ascii=False, indent=2))
            else:
                click.echo(click.style("[安全预览模式] 待提交订单：", fg='yellow'))
                click.echo(f"  账户: {account_id}")
                click.echo(f"  股票: {stock} | 方向: {order_type} | 数量: {volume} | 价格: {price}")
                click.echo(click.style("若确认下单，请追加 --confirm 参数执行提交。", fg='cyan'))
            return

        direction_code = 0 if order_type == 'buy' else 1
        res = session.tdx.order_stock(
            account_id=account_id,
            stock_code=stock.strip(),
            order_type=direction_code,
            order_volume=volume,
            price_type=price_type,
            price=price
        )
        if as_json:
            click.echo(json.dumps({"success": True, "order_result": res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"委托已提交: {res}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "下单失败")


@trade.command('cancel')
@click.option('--order-id', '-o', required=True, help='待撤订单号')
@click.option('--stock', '-s', default='', help='对应股票代码')
@click.option('--account-id', '-a', type=int, default=None, help='账户 ID')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def trade_cancel(ctx, order_id: str, stock: str, account_id: Optional[int], as_json: bool):
    """撤销已提交的委托订单"""
    try:
        session.ensure_initialized()
        if account_id is None:
            account_id = session.tdx.stock_account()
        if account_id is None or account_id < 0:
            raise TdxError("未检测到有效登录的交易账户，请先在通达信客户端登录交易账户")

        res = session.tdx.cancel_order_stock(
            account_id=account_id,
            stock_code=stock.strip(),
            order_id=order_id.strip()
        )
        if as_json:
            click.echo(json.dumps({"success": True, "cancel_result": res}, ensure_ascii=False, indent=2))
        else:
            click.echo(click.style(f"撤单指令已发送: {res}", fg='green'))
    except Exception as e:
        _handle_error(ctx, e, as_json, "撤单失败")


# ==================== 配置与状态命令组 ====================

@cli.group('config')
def config_cmd():
    """配置管理命令"""
    pass


@config_cmd.command('show')
@click.pass_context
def config_show(ctx):
    """显示当前通达信路径与环境配置"""
    cfg = ctx.obj.get('config', config)
    status_info = cfg.get_status()

    click.echo("=== 通达信配置与路径状态 ===")
    for k, v in status_info.items():
        icon = "[OK]" if v else "[X]" if k.endswith('_exists') else ""
        click.echo(f"{k}: {v} {icon}")


@config_cmd.command('set')
@click.option('--tdx-path', required=True, help='通达信安装路径 (如 D:\\tdx)')
@click.pass_context
def config_set(ctx, tdx_path: str):
    """设置通达信安装路径 (自动持久化至 ~/.tdx_tq/config.json)"""
    config.tdx_path = tdx_path
    saved_file = config.save()
    click.echo(click.style(f"通达信路径已成功设置为：{tdx_path} (已持久化保存至 {saved_file})", fg='green'))


@cli.command()
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def status(ctx, as_json: bool):
    """查看当前 CLI 连接状态与通达信内核状态"""
    result = {
        'session_initialized': session.is_initialized,
        'config': config.get_status()
    }

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.echo("=== TDX TQ SDK Status ===")
        click.echo(f"Session initialized: {session.is_initialized}")
        click.echo(f"\nConfig:")
        for k, v in config.get_status().items():
            icon = "[OK]" if v else "[X]" if k.endswith('_exists') else ""
            click.echo(f"  {k}: {v} {icon}")


def main():
    """CLI 入口点"""
    cli(obj={})


if __name__ == '__main__':
    main()
