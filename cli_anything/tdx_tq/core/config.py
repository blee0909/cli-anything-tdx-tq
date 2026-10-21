"""
配置管理模块

管理 CLI 配置和路径设置。
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any


class Config:
    """CLI 配置管理类，支持环境变量与 ~/.tdx_tq/config.json 文件持久化"""

    # 默认通达信路径
    DEFAULT_TDX_PATH = r'D:\tdx'
    DEFAULT_CONFIG_DIR = Path.home() / '.tdx_tq'
    DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / 'config.json'

    def __init__(self, config_file: Optional[Path] = None):
        self._config_file = config_file or self.DEFAULT_CONFIG_FILE
        self._tdx_path: Optional[str] = None
        self._pyplugins_path: Optional[str] = None
        self._sys_path: Optional[str] = None
        self._load_from_file()

    def _load_from_file(self) -> None:
        """从配置文件加载设置"""
        if self._config_file and self._config_file.exists():
            try:
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        saved_path = data.get('tdx_path')
                        if saved_path:
                            self._tdx_path = saved_path
            except Exception:
                pass

    def save(self) -> Path:
        """将当前配置持久化到配置文件"""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'tdx_path': self.tdx_path
        }
        with open(self._config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return self._config_file

    @property
    def config_file_path(self) -> str:
        """获取配置文件路径"""
        return str(self._config_file)

    @property
    def tdx_path(self) -> str:
        """获取通达信安装路径 (环境变量 > 持久化配置 > 默认路径)"""
        return os.environ.get('TDX_PATH') or self._tdx_path or self.DEFAULT_TDX_PATH

    @tdx_path.setter
    def tdx_path(self, value: str):
        self._tdx_path = value
        os.environ['TDX_PATH'] = value

    @property
    def pyplugins_path(self) -> str:
        """获取插件路径"""
        return self._pyplugins_path or os.path.join(self.tdx_path, 'PYPlugins')

    @property
    def sys_path(self) -> str:
        """获取系统插件路径"""
        return self._sys_path or os.path.join(self.pyplugins_path, 'sys')

    def validate_paths(self) -> bool:
        """验证路径是否存在"""
        paths = [
            self.tdx_path,
            self.pyplugins_path,
            self.sys_path
        ]
        return all(os.path.exists(p) for p in paths)

    def get_status(self) -> Dict[str, Any]:
        """获取配置状态"""
        return {
            'tdx_path': self.tdx_path,
            'tdx_exists': os.path.exists(self.tdx_path),
            'pyplugins_path': self.pyplugins_path,
            'pyplugins_exists': os.path.exists(self.pyplugins_path),
            'sys_path': self.sys_path,
            'sys_exists': os.path.exists(self.sys_path),
            'config_file': str(self._config_file),
            'config_file_exists': self._config_file.exists()
        }


# 全局配置实例
config = Config()

