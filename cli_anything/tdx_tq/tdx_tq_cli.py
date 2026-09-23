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


@click.group()
@click.version_option(version='1.0.0', prog_name='cli-anything-tdx-tq')
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
@click.option('--stocks', '-s', required=True, help='股票代码列表，逗号分隔 (例: 600519.SH,000001.SZ)')
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
    """获取 K 线历史数据 (完整保留交易日期与 OHLCV 指标)"""
    try:
        session.ensure_initialized()
        stock_list = [s.strip() for s in stocks.split(',') if s.strip()]
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
        click.echo(click.style(f"获取 K 线数据失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@market.command('snapshot')
@click.option('--stock', '-s', required=True, help='股票代码 (例: 600519.SH)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def market_snapshot(ctx, stock: str, as_json: bool):
    """获取实时五档行情快照"""
    try:
        session.ensure_initialized()
        snapshot = session.tdx.get_market_snapshot(stock.strip())

        if as_json:
            click.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 实时快照 ===")
            for k, v in snapshot.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(click.style(f"获取行情快照失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
@click.option('--stock', '-s', required=True, help='股票代码')
@click.option('--count', '-c', default=5, type=int, help='获取记录数 (默认 5)')
@click.option('--json', 'as_json', is_flag=True, help='以 JSON 格式输出')
@click.pass_context
def stock_gb(ctx, stock: str, count: int, as_json: bool):
    """获取股本变动历史信息"""
    try:
        session.ensure_initialized()
        data = session.tdx.get_gb_info(stock_code=stock.strip(), count=count)

        if as_json:
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            click.echo(f"=== {stock} 股本信息 ===")
            click.echo(data)
    except Exception as e:
        click.echo(click.style(f"获取股本信息失败：{e}", fg='red'), err=True)
        ctx.exit(1)


@stock.command('kzz')
@click.option('--stock', '-s', required=True, help='可转债代码 (例: 123054.SZ)')
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
            for k, v in data.items():
                click.echo(f"{k}: {v}")
    except Exception as e:
        click.echo(click.style(f"获取可转债信息失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
        click.echo(click.style(f"更新板块成分股失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
        click.echo(click.style(f"调用选股公式失败：{e}", fg='red'), err=True)
        ctx.exit(1)


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
    """设置通达信安装路径"""
    config.tdx_path = tdx_path
    click.echo(click.style(f"通达信路径已成功设置为：{tdx_path}", fg='green'))


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
