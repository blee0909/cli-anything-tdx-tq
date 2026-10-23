"""
CLI E2E 测试

测试完整的命令行接口流程与真实数据获取。
"""

import pytest
import subprocess
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _resolve_cli(cli_name: str) -> list:
    """
    解析 CLI 命令路径，优先使用当前虚拟环境中的可执行文件
    """
    venv_cli = Path(sys.executable).parent / f"{cli_name}.exe"
    if venv_cli.exists():
        return [str(venv_cli)]
    return [cli_name]


def _run_cli(args: list) -> subprocess.CompletedProcess:
    """运行 CLI 命令，强制 UTF-8 编码与容错处理"""
    cli = [sys.executable, '-m', 'cli_anything.tdx_tq.tdx_tq_cli']
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    return subprocess.run(
        cli + args,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        shell=False,
        timeout=45
    )


class TestCLIBase:
    """测试 CLI 基础命令"""

    def test_cli_version(self):
        """测试版本号"""
        result = _run_cli(['--version'])
        assert result.returncode == 0
        assert '1.1.0' in result.stdout

    def test_cli_help(self):
        """测试帮助信息"""
        result = _run_cli(['--help'])
        assert result.returncode == 0
        assert 'market' in result.stdout
        assert 'stock' in result.stdout
        assert 'sector' in result.stdout
        assert 'financial' in result.stdout
        assert 'formula' in result.stdout
        assert 'client' in result.stdout
        assert 'trade' in result.stdout

    def test_cli_status(self):
        """测试状态查询"""
        result = _run_cli(['status', '--json'])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert 'session_initialized' in data
        assert 'config' in data
        assert data['config']['tdx_path'] == os.environ.get('TDX_PATH', r'D:\tdx')


class TestCLIMarket:
    """测试行情数据命令"""

    def test_market_get_help(self):
        """测试获取行情帮助"""
        result = _run_cli(['market', 'get', '--help'])
        assert result.returncode == 0
        assert '--stocks' in result.stdout
        assert '--period' in result.stdout

    def test_market_snapshot_help(self):
        """测试行情快照帮助"""
        result = _run_cli(['market', 'snapshot', '--help'])
        assert result.returncode == 0
        assert '--stock' in result.stdout

    @pytest.mark.live
    def test_market_snapshot_live(self):
        """实测行情快照数据获取"""
        result = _run_cli(['market', 'snapshot', '-s', '600519.SH', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert data.get('ErrorId') == '0'
        assert 'Now' in data or 'LastClose' in data

    @pytest.mark.live
    def test_market_get_live(self):
        """实测 K 线数据获取与日期保留"""
        result = _run_cli(['market', 'get', '-s', '600519.SH', '-c', '3', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert '600519.SH' in data
        bars = data['600519.SH']
        assert len(bars) > 0
        assert 'date' in bars[0]
        assert 'close' in bars[0]

    @pytest.mark.live
    def test_market_divid_live_preserves_dates(self):
        result = _run_cli(['market', 'divid', '-s', '600519.SH', '--start', '20240101', '--end', '20241231', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        records = json.loads(result.stdout)
        assert records, 'Historical dividend records must be available for this live fixture'
        for row in records:
            assert datetime.fromisoformat(row['Date']).year == 2024, row
            assert 'Bonus' in row


class TestCLIStock:
    """测试股票信息命令"""

    def test_stock_info_help(self):
        """测试股票信息帮助"""
        result = _run_cli(['stock', 'info', '--help'])
        assert result.returncode == 0
        assert '--stock' in result.stdout

    @pytest.mark.live
    def test_stock_info_live(self):
        """实测股票信息与中文解码"""
        result = _run_cli(['stock', 'info', '-s', '600519.SH', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert data.get('Name') == '贵州茅台'

    @pytest.mark.live
    def test_stock_search_live(self):
        """实测股票代码与名称模糊搜索"""
        result = _run_cli(['stock', 'search', '-k', '茅台', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert any(item.get('Code') == '600519.SH' for item in data)

    @pytest.mark.live
    def test_stock_gb_live(self):
        """实测股本变动信息与有效交易日匹配"""
        result = _run_cli(['stock', 'gb', '-s', '600519.SH', '-c', '3', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) > 0
        assert 'Ltgb' in data[0]
        assert 'Zgb' in data[0]


class TestCLISector:
    """测试板块数据命令"""

    def test_sector_list_help(self):
        """测试板块列表帮助"""
        result = _run_cli(['sector', 'list', '--help'])
        assert result.returncode == 0
        assert '--type' in result.stdout

    @pytest.mark.live
    def test_sector_list_live(self):
        """实测行业板块列表"""
        result = _run_cli(['sector', 'list', '-t', '0', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        sectors = json.loads(result.stdout)
        assert len(sectors) > 10


class TestCLIFormula:
    """测试公式系统命令"""

    @pytest.mark.live
    def test_formula_list_live(self):
        """实测公式列表获取"""
        result = _run_cli(['formula', 'list', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        formulas = json.loads(result.stdout)
        assert len(formulas) > 50
        assert any(f.get('acCode') == 'MA' for f in formulas)


class TestCLITrade:
    """测试交易系统命令"""

    def test_trade_help(self):
        """测试交易命令帮助"""
        result = _run_cli(['trade', '--help'])
        assert result.returncode == 0
        assert 'account' in result.stdout
        assert 'order' in result.stdout
        assert 'asset' in result.stdout

    @pytest.mark.live
    def test_trade_account_live(self):
        """实测交易账户状态查询"""
        result = _run_cli(['trade', 'account', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert 'logged_in' in data
        assert isinstance(data['logged_in'], bool)

    def test_trade_order_preview(self):
        """实测交易委托安全预览模式"""
        result = _run_cli(['trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '1300.0', '-a', '1', '--json'])
        assert result.returncode == 0, result.stderr or result.stdout
        data = json.loads(result.stdout)
        assert data.get('status') == 'preview'
        assert data['order']['stock'] == '600519.SH'


class TestBatchReadContracts:
    @pytest.mark.live
    def test_historical_one_day_range_is_not_recent_tail(self):
        result = _run_cli(['market','get','-s','600519.SH','--start','20261020','--end','20261020','--json'])
        assert result.returncode == 0, result.stdout or result.stderr
        bars = json.loads(result.stdout)['600519.SH']
        assert len(bars) == 1 and bars[0]['date'].startswith('2026-10-20')

    @pytest.mark.live
    @pytest.mark.parametrize('allow_partial', [False,True])
    def test_batch_with_unavailable_fixture_is_explicit(self, allow_partial):
        # This fixture has no usable close values in the verification client.
        args = ['market','get','-s','600519.SH,999999.SZ','-c','2','--json']
        result = _run_cli(args + (['--allow-partial'] if allow_partial else []))
        data = json.loads(result.stdout)
        assert result.returncode == (0 if allow_partial else 1), result.stdout
        quality = data if allow_partial else data['details']
        assert quality['partial'] is True and quality['missing_stocks'] == ['999999.SZ']
        assert list(data['data']) == ['600519.SH'] and len(data['data']['600519.SH']) == 2
        if not allow_partial:
            assert data['error_code'] == 'DATA_INCOMPLETE'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
