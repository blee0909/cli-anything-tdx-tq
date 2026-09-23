"""
CLI E2E 测试

测试完整的命令行接口流程与真实数据获取。
"""

import pytest
import subprocess
import json
import os
import sys
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
    cli = _resolve_cli("cli-anything-tdx-tq")
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    return subprocess.run(
        cli + args,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        shell=True
    )


class TestCLIBase:
    """测试 CLI 基础命令"""

    def test_cli_version(self):
        """测试版本号"""
        result = _run_cli(['--version'])
        assert result.returncode == 0
        assert '1.0.0' in result.stdout

    def test_cli_help(self):
        """测试帮助信息"""
        result = _run_cli(['--help'])
        assert result.returncode == 0
        assert 'market' in result.stdout
        assert 'stock' in result.stdout
        assert 'sector' in result.stdout
        assert 'financial' in result.stdout
        assert 'formula' in result.stdout

    def test_cli_status(self):
        """测试状态查询"""
        result = _run_cli(['status', '--json'])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert 'session_initialized' in data
        assert 'config' in data
        assert data['config']['tdx_path'] == r'D:\tdx'


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

    def test_market_snapshot_live(self):
        """实测行情快照数据获取"""
        result = _run_cli(['market', 'snapshot', '-s', '600519.SH', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        data = json.loads(result.stdout)
        assert data.get('ErrorId') == '0'
        assert 'Now' in data or 'LastClose' in data

    def test_market_get_live(self):
        """实测 K 线数据获取与日期保留"""
        result = _run_cli(['market', 'get', '-s', '600519.SH', '-c', '3', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        data = json.loads(result.stdout)
        assert '600519.SH' in data
        bars = data['600519.SH']
        assert len(bars) > 0
        assert 'date' in bars[0]
        assert 'close' in bars[0]


class TestCLIStock:
    """测试股票信息命令"""

    def test_stock_info_help(self):
        """测试股票信息帮助"""
        result = _run_cli(['stock', 'info', '--help'])
        assert result.returncode == 0
        assert '--stock' in result.stdout

    def test_stock_info_live(self):
        """实测股票信息与中文解码"""
        result = _run_cli(['stock', 'info', '-s', '600519.SH', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        data = json.loads(result.stdout)
        assert data.get('Name') == '贵州茅台'

    def test_stock_search_live(self):
        """实测股票代码与名称模糊搜索"""
        result = _run_cli(['stock', 'search', '-k', '茅台', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        data = json.loads(result.stdout)
        assert any(item.get('Code') == '600519.SH' for item in data)


class TestCLISector:
    """测试板块数据命令"""

    def test_sector_list_help(self):
        """测试板块列表帮助"""
        result = _run_cli(['sector', 'list', '--help'])
        assert result.returncode == 0
        assert '--type' in result.stdout

    def test_sector_list_live(self):
        """实测行业板块列表"""
        result = _run_cli(['sector', 'list', '-t', '0', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        sectors = json.loads(result.stdout)
        assert len(sectors) > 10


class TestCLIFormula:
    """测试公式系统命令"""

    def test_formula_list_live(self):
        """实测公式列表获取"""
        result = _run_cli(['formula', 'list', '--json'])
        if result.returncode != 0:
            pytest.skip("通达信未开启或未登录，跳过实时数据测试")
        formulas = json.loads(result.stdout)
        assert len(formulas) > 50
        assert any(f.get('acCode') == 'MA' for f in formulas)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
