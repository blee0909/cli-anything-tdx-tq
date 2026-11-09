"""Validated configuration with explicit precedence and atomic persistence."""
import json
import os
import tempfile
from pathlib import Path

from .errors import TdxConfigError

COMMON_TDX_PATHS = (r'D:\TDX64', r'D:\tdx', r'C:\new_tdx', r'D:\new_tdx', r'C:\tdx')


def _safe_is_dir(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _safe_exists(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).exists()
    except OSError:
        return False


def discover_tdx_paths() -> list[dict]:
    return Config.discover_tdx_paths()


class Config:
    DEFAULT_TDX_PATH = r'D:\tdx'
    DEFAULT_CONFIG_DIR = Path.home() / '.tdx_tq'
    DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / 'config.json'

    def __init__(self, config_file: Path | None = None):
        self._config_file = Path(config_file or os.environ.get('TDX_CONFIG_FILE') or self.DEFAULT_CONFIG_FILE)
        self._tdx_path = None
        self._override_path = None
        self.load_error = None
        self._load_from_file()

    @staticmethod
    def _validated_path(value):
        if not isinstance(value, str) or not value.strip():
            raise TdxConfigError('tdx_path 必须为非空路径字符串')
        return value.strip()

    def _load_from_file(self) -> None:
        if not _safe_exists(self._config_file):
            return
        try:
            data = json.loads(self._config_file.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or 'tdx_path' not in data:
                raise TdxConfigError('配置必须为包含 tdx_path 的 JSON 对象')
            self._tdx_path = self._validated_path(data['tdx_path'])
        except (OSError, ValueError, TdxConfigError) as exc:
            self.load_error = str(exc)

    def ensure_valid(self) -> None:
        if self.load_error and self._override_path is None and not os.environ.get('TDX_PATH'):
            raise TdxConfigError(f"配置文件无效 {self._config_file}: {self.load_error}；可使用 config set 修复")

    def override(self, value: str | None) -> None:
        self._override_path = self._validated_path(value) if value is not None else None

    def save(self) -> Path:
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._config_file.parent,
                                             prefix='.config-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({'tdx_path': self.tdx_path}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._config_file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.load_error = None
        return self._config_file

    @classmethod
    def discover_tdx_paths(cls) -> list[dict]:
        discovered = []
        for candidate in COMMON_TDX_PATHS:
            p = Path(candidate)
            sys_file = p / 'PYPlugins' / 'sys' / 'tqcenter.py'
            user_file = p / 'PYPlugins' / 'user' / 'tqcenter.py'
            has_sys = _safe_is_file(sys_file)
            has_user = _safe_is_file(user_file)
            if has_sys or has_user:
                plugin = 'sys' if has_sys else 'user'
                target = str(sys_file) if has_sys else str(user_file)
                discovered.append({
                    'path': candidate,
                    'plugin_type': plugin,
                    'tqcenter_path': target,
                    'has_sys': has_sys,
                    'has_user': has_user,
                })
        return discovered

    @property
    def config_file_path(self) -> str:
        return str(self._config_file)

    @property
    def tdx_path(self) -> str:
        return self._override_path or os.environ.get('TDX_PATH') or self._tdx_path or self.DEFAULT_TDX_PATH

    @tdx_path.setter
    def tdx_path(self, value: str):
        self._tdx_path = self._validated_path(value)
        self._override_path = self._tdx_path

    @property
    def source(self) -> str:
        if self._override_path is not None:
            return 'command_line'
        return 'environment' if os.environ.get('TDX_PATH') else 'file' if self._tdx_path else 'default'

    @property
    def pyplugins_path(self) -> str:
        return os.path.join(self.tdx_path, 'PYPlugins')

    @property
    def sys_path(self) -> str:
        sys_dir = os.path.join(self.pyplugins_path, 'sys')
        user_dir = os.path.join(self.pyplugins_path, 'user')
        if not _safe_is_dir(sys_dir) and _safe_is_dir(user_dir):
            return user_dir
        return sys_dir

    def validate_paths(self) -> bool:
        return all(_safe_is_dir(p) for p in (self.tdx_path, self.pyplugins_path, self.sys_path))

    def get_status(self) -> dict:
        return {'tdx_path': self.tdx_path, 'tdx_exists': _safe_is_dir(self.tdx_path),
                'pyplugins_path': self.pyplugins_path, 'pyplugins_exists': _safe_is_dir(self.pyplugins_path),
                'sys_path': self.sys_path, 'sys_exists': _safe_is_dir(self.sys_path),
                'config_file': str(self._config_file), 'config_file_exists': _safe_exists(self._config_file),
                'source': self.source, 'load_error': self.load_error,
                'discovered_paths': self.discover_tdx_paths()}


config = Config()
