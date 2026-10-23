"""
配置管理模块单元测试
"""

import pytest
import os
from pathlib import Path
import sys

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from cli_anything.tdx_tq.core.config import Config


class TestConfig:
    """配置管理测试类"""

    def setup_method(self):
        """每个测试前的准备工作"""
        os.environ.pop('TDX_PATH', None)
        self.config = Config()

    def teardown_method(self):
        """测试后清理环境"""
        os.environ.pop('TDX_PATH', None)

    def test_config_default_paths(self):
        """测试默认路径配置"""
        assert self.config.tdx_path == os.environ.get('TDX_PATH', r'D:\tdx')
        assert self.config.pyplugins_path == r'D:\tdx\PYPlugins'
        assert self.config.sys_path == r'D:\tdx\PYPlugins\sys'

    def test_config_custom_path(self):
        """测试自定义路径配置"""
        custom_path = r'D:\custom_tdx'
        self.config.tdx_path = custom_path
        assert self.config.tdx_path == custom_path
        assert self.config.source == 'command_line'
        assert self.config.pyplugins_path == r'D:\custom_tdx\PYPlugins'
        assert self.config.sys_path == r'D:\custom_tdx\PYPlugins\sys'

    def test_config_get_status(self):
        """测试获取配置状态"""
        status = self.config.get_status()
        assert 'tdx_path' in status
        assert 'tdx_exists' in status
        assert 'pyplugins_path' in status
        assert 'pyplugins_exists' in status
        assert 'sys_path' in status
        assert 'sys_exists' in status

    def test_config_validate_paths(self):
        """测试路径验证"""
        result = self.config.validate_paths()
        assert isinstance(result, bool)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
