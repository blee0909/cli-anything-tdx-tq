"""Process-local session, initialized only after configuration is resolved."""
import datetime
import os
from pathlib import Path

from .config import config
from .errors import TdxDataError, TdxError, TdxInitError, TdxParamError
from .sdk import TqAdapter, load_sdk


class Session:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tdx = None
            cls._instance._initialized = False
            cls._instance._loaded_path = None
            cls._instance._state = 'uninitialized'
        return cls._instance

    @property
    def state(self) -> str:
        return self._state

    @property
    def tdx(self):
        if self._tdx is None:
            self._ensure_tq_module()
        return self._tdx

    @property
    def is_initialized(self) -> bool:
        return self._initialized and getattr(self._tdx, 'run_id', -1) >= 0

    def _ensure_tq_module(self):
        path = str(Path(config.tdx_path).expanduser().resolve())
        if self._loaded_path is not None and self._loaded_path != path:
            raise TdxInitError("当前进程的 SDK 路径与配置不一致，请在新进程中切换路径")
        if self._tdx is None:
            self._tdx = TqAdapter(load_sdk(path))
            self._loaded_path = path

    def initialize(self, script_path: str | None = None) -> None:
        config.ensure_valid()
        self._state = 'initializing'
        try:
            self._ensure_tq_module()
            if self.is_initialized:
                self._state = 'ready'
                return
            # Each process owns its connection identity; parallel CLIs must not share it.
            identity = script_path or f"{os.path.abspath(__file__)}.{os.getpid()}"
            self._tdx.initialize(identity)
            if getattr(self._tdx, 'run_id', -1) < 0:
                raise TdxInitError("通达信连接失败 (run_id < 0)，请检查客户端登录状态和 SDK 路径")
            self._initialized = True
            self._state = 'ready'
        except Exception as exc:
            self._state = 'failed'
            self._initialized = False
            if self._tdx is not None:
                try:
                    raw = getattr(self._tdx, 'raw', None)
                    if raw is not None and hasattr(raw, '_release'):
                        raw._release()
                    elif hasattr(self._tdx, 'close'):
                        self._tdx.close()
                except Exception:
                    pass
            if isinstance(exc, TdxError):
                raise
            raise TdxInitError(f"通达信初始化失败: {exc}") from exc

    def close(self) -> None:
        self._state = 'closing'
        try:
            if self._initialized and self._tdx is not None:
                self._tdx.close()
                raw = getattr(self._tdx, 'raw', None)
                if getattr(raw, '_initialized', False):
                    self._state = 'failed'
                    self._initialized = False
                    raise TdxInitError("底层 SDK 未确认关闭连接")
            self._state = 'closed'
        except Exception:
            self._state = 'failed'
            raise
        finally:
            self._initialized = False

    def ensure_initialized(self) -> None:
        if not self.is_initialized:
            self.initialize()
        elif self._loaded_path != str(Path(config.tdx_path).expanduser().resolve()):
            raise TdxInitError("SDK 路径已改变，请在新进程中重新连接")

    def get_recent_trading_dates(self, count: int = 5, market: str = 'SH') -> list:
        if count <= 0:
            raise TdxParamError("交易日数量必须大于零")
        self.ensure_initialized()
        if market == 'AG' and getattr(self.tdx, 'profile', '') == 'user_tpythclient':
            market = 'SH'
        dates = self.tdx.get_trading_dates(market=market, start_time='', end_time='', count=count)
        if not isinstance(dates, (list, tuple)) or not dates:
            raise TdxDataError("交易日历不可用，无法推算真实交易日期（请检查上证指数(999999)盘后数据或数据服务）")
        validated = []
        for value in dates:
            value = str(value)
            try:
                date = datetime.datetime.strptime(value, '%Y%m%d')
            except ValueError as exc:
                raise TdxDataError(f"SDK 返回无效交易日: {value}") from exc
            if date.weekday() >= 5:
                raise TdxDataError(f"SDK 返回非交易日: {value}")
            if value not in validated:
                validated.append(value)
        return sorted(validated)[-count:]

    def diagnostics(self) -> dict:
        info = {'configured_path': config.tdx_path, 'loaded_module_path': None, 'dll_path': None}
        if isinstance(self._tdx, TqAdapter):
            info.update(self._tdx.diagnostics())
        return info


session = Session()
