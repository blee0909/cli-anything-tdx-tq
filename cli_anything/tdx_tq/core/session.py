"""
会话管理模块

管理与通达信 TQ 客户端 (tqcenter) 的连接状态和生命周期。
集成 GBK 编码自动修复与标准输出清洗机制，确保 CLI 输出完全纯净。
"""

import sys
import os
import builtins
from typing import Optional, Dict, Any
from pathlib import Path

from .config import config


class TdxError(Exception):
    """通达信接口基础异常"""
    pass


class TdxInitError(TdxError):
    """初始化/连接错误"""
    pass


class TdxDataError(TdxError):
    """数据获取错误"""
    pass


class TdxParamError(TdxError):
    """参数错误"""
    pass


def _setup_tdx_paths():
    """将通达信插件路径加入 sys.path"""
    pyplugins_dir = config.pyplugins_path
    sys_dir = config.sys_path

    if os.path.exists(pyplugins_dir) and pyplugins_dir not in sys.path:
        sys.path.insert(0, pyplugins_dir)
    if os.path.exists(sys_dir) and sys_dir not in sys.path:
        sys.path.insert(0, sys_dir)


_setup_tdx_paths()


def _patch_tqcenter_module(mod):
    """配置 tqcenter 模块：静默输出并修复 DLL 中文编码"""
    if mod is None:
        return

    # 1. 过滤内部连接调试 print，避免污染终端输出与 JSON 解析
    def quiet_print(*args, **kwargs):
        if args and isinstance(args[0], str) and any(kw in args[0] for kw in ['TQ数据', '初始化成功', '已关闭', 'TQ数据连接']):
            return
        kwargs['file'] = sys.stderr
        builtins.print(*args, **kwargs)

    mod.print = quiet_print

    # 2. 补丁修复通达信 DLL 中文字符编码 (GBK 转 UTF-8)
    dll = getattr(mod, 'dll', None)
    if dll is not None and not getattr(dll, '_gbk_patched', False):
        dll_funcs = ['GetTdxDataStr', 'GetProDataInStr', 'TdxFuncMain', 'GetOrderStr', 'SetMsgToMain']
        for func_name in dll_funcs:
            if hasattr(dll, func_name):
                orig_func = getattr(dll, func_name)

                def make_wrapper(fn):
                    def wrapper(*args, **kwargs):
                        ptr = fn(*args, **kwargs)
                        if ptr:
                            try:
                                ptr.decode('utf-8')
                                return ptr
                            except UnicodeDecodeError:
                                try:
                                    return ptr.decode('gbk').encode('utf-8')
                                except Exception:
                                    return ptr
                        return ptr
                    return wrapper

                setattr(dll, func_name, make_wrapper(orig_func))
        setattr(dll, '_gbk_patched', True)


try:
    import tqcenter
    _patch_tqcenter_module(tqcenter)
    tq = tqcenter.tq
    tqconst = tqcenter.tqconst
except ImportError:
    tq = None
    tqconst = None


class Session:
    """CLI 会话管理类"""

    _instance: Optional['Session'] = None
    _tdx: Any = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        pass

    @property
    def tdx(self) -> Any:
        """获取 tq 实例"""
        if self._tdx is None:
            self._ensure_tq_module()
            self._tdx = tq
        return self._tdx

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized and getattr(self._tdx, 'run_id', -1) >= 0

    def _ensure_tq_module(self):
        """确保 tqcenter 模块已加载并完成补丁"""
        global tq, tqconst
        if tq is None:
            _setup_tdx_paths()
            try:
                import tqcenter
                _patch_tqcenter_module(tqcenter)
                tq = tqcenter.tq
                tqconst = tqcenter.tqconst
            except Exception as e:
                raise TdxInitError(
                    f"无法加载通达信 TQ 插件: {e}\n"
                    f"当前通达信路径: {config.tdx_path}\n"
                    f"请确保通达信安装在 {config.tdx_path} 且包含 PYPlugins/sys/tqcenter.py。"
                )
        self._tdx = tq

    def initialize(self, script_path: Optional[str] = None) -> None:
        """
        初始化连接

        Args:
            script_path: 脚本标识路径
        """
        if self._initialized and getattr(self._tdx, 'run_id', -1) >= 0:
            return

        self._ensure_tq_module()

        if not script_path:
            script_path = os.path.abspath(__file__)

        try:
            self._tdx.initialize(script_path)
        except Exception as e:
            raise TdxInitError(f"通达信 TQ 连接初始化异常: {e}")

        run_id = getattr(self._tdx, 'run_id', -1)
        if run_id < 0:
            raise TdxInitError(
                "通达信连接失败 (run_id < 0)。\n"
                "请检查：\n"
                "1. 通达信客户端 (TdxW.exe) 是否已启动并登录成功\n"
                f"2. 通达信安装路径是否正确 (当前: {config.tdx_path})\n"
                "3. 是否有其他同名脚本正在占用连接"
            )

        self._initialized = True

    def close(self) -> None:
        """关闭连接"""
        if self._initialized and self._tdx is not None:
            try:
                self._tdx.close()
            except Exception:
                pass
            finally:
                self._initialized = False

    def ensure_initialized(self) -> None:
        """确保已初始化"""
        if not self.is_initialized:
            self.initialize()

    def __del__(self):
        if self._initialized:
            self.close()


# 全局会话实例
session = Session()
