"""
会话管理模块单元测试
"""

import pytest
import os
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from cli_anything.tdx_tq.core.session import Session, session, TdxError, TdxInitError
from cli_anything.tdx_tq.core.config import config


class TestSession:
    """会话管理测试类"""

    def test_session_singleton(self):
        """测试 Session 为单例模式"""
        s1 = Session()
        s2 = Session()
        assert s1 is s2
        assert s1 is session

    def test_session_initial_state(self):
        """测试初始状态属性"""
        assert isinstance(session.is_initialized, bool)

    def test_session_initialize_and_close(self):
        """测试初始化与正常关闭"""
        if not os.path.exists(config.sys_path):
            pytest.skip("通达信安装路径不存在，跳过联调测试")

        session.initialize()
        assert session.is_initialized is True
        assert session.tdx is not None
        assert session.tdx.run_id >= 0

        # 关闭连接
        session.close()
        assert session.is_initialized is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
