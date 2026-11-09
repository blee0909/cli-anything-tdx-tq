"""Non-paid Local TDX Cache & Download File Parsers."""
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .errors import TdxDataError, TdxParamError
from .market import stock_code

logger = logging.getLogger('cli_anything.tdx_tq')


def search_local_stocks(tdx_path: str, keyword: str, *, limit: int = 50) -> list[dict[str, str]]:
    """Search stock symbols and names in local TDX hq_cache without network/SDK.

    Scans shs.tnf, szs.tnf, bjs.tnf and optionally code2name_hk.ini / code2name.ini.
    Matches keyword against Code, Full Code, Name, or Pinyin (case-insensitive).
    """
    kw = (keyword or '').strip()
    if not kw:
        raise TdxParamError("搜索关键词不能为空")
    if not isinstance(limit, int) or limit < 1:
        raise TdxParamError("limit 必须为正整数")

    hq_cache = Path(tdx_path).resolve() / "T0002" / "hq_cache"
    if not hq_cache.is_dir():
        raise TdxDataError(f"未找到通达信本地行情缓存目录: {hq_cache}")

    kw_upper = kw.upper()
    results: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    # 1. Parse A-share / Beijing binary .tnf caches
    tnf_files = [
        ('shs.tnf', '.SH'),
        ('szs.tnf', '.SZ'),
        ('bjs.tnf', '.BJ'),
    ]
    for filename, suffix in tnf_files:
        fpath = hq_cache / filename
        if not fpath.is_file():
            continue
        try:
            with open(fpath, 'rb') as f:
                header = f.read(50)
                if len(header) < 50:
                    continue
                rec_len = 360
                while True:
                    rec = f.read(rec_len)
                    if len(rec) < rec_len:
                        break
                    code = rec[0:6].decode('ascii', errors='ignore').strip('\x00').strip()
                    if not code:
                        continue
                    name = rec[31:77].split(b'\x00')[0].decode('gbk', errors='ignore').strip()
                    pinyin = rec[329:338].split(b'\x00')[0].decode('ascii', errors='ignore').strip()
                    full_code = f"{code}{suffix}"

                    if full_code in seen_codes:
                        continue

                    if (
                        kw_upper in code.upper()
                        or kw_upper in full_code.upper()
                        or kw_upper in name.upper()
                        or kw_upper in pinyin.upper()
                    ):
                        seen_codes.add(full_code)
                        results.append({
                            'Code': full_code,
                            'Name': name,
                            'Pinyin': pinyin,
                            'source': 'local_tdx_cache',
                        })
                        if len(results) >= limit:
                            return results
        except Exception as exc:
            logger.debug("读取 %s 异常: %s", fpath, exc)

    # 2. Optionally scan HK / Index / Futures ini files
    ini_files = [
        ('code2name_hk.ini', '.HK'),
        ('code2name.ini', ''),
    ]
    for ini_name, suffix in ini_files:
        ini_path = hq_cache / ini_name
        if not ini_path.is_file():
            continue
        try:
            with open(ini_path, 'r', encoding='gbk', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('['):
                        continue
                    parts = line.split(',')
                    if len(parts) >= 2:
                        raw_code = parts[0].strip()
                        name = parts[1].strip()
                        full_code = f"{raw_code}{suffix}" if suffix and not raw_code.endswith(suffix) else raw_code
                        if full_code in seen_codes:
                            continue
                        if (
                            kw_upper in raw_code.upper()
                            or kw_upper in full_code.upper()
                            or kw_upper in name.upper()
                        ):
                            seen_codes.add(full_code)
                            results.append({
                                'Code': full_code,
                                'Name': name,
                                'Pinyin': '',
                                'source': 'local_tdx_cache',
                            })
                            if len(results) >= limit:
                                return results
        except Exception as exc:
            logger.debug("读取 %s 异常: %s", ini_path, exc)

    return results


def lookup_local_relations(tdx_path: str, symbol: str) -> list[dict[str, Any]]:
    """Look up sectors and blocks for a symbol from local TDX config and dat files.

    Parses:
    - tdxzs3.cfg (industry, research industry, index codes)
    - tdxhy.cfg (stock -> TDX industry and research industry mappings)
    - infoharbor_block.dat (concept #GN, style #FG, index #ZS block memberships)
    """
    valid_symbol = stock_code(symbol)
    code6, suffix = valid_symbol.split('.', 1)

    market_prefix_map = {'SZ': '0', 'SH': '1', 'BJ': '2'}
    market_prefix = market_prefix_map.get(suffix, '1')
    target_token = f"{market_prefix}#{code6}"

    hq_cache = Path(tdx_path).resolve() / "T0002" / "hq_cache"
    if not hq_cache.is_dir():
        raise TdxDataError(f"未找到通达信本地行情缓存目录: {hq_cache}")

    results: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    # 1. Parse tdxzs3.cfg: name|88xxxx|type|...|key
    zs_map: dict[str, tuple[str, str, str]] = {}
    zs3_file = hq_cache / 'tdxzs3.cfg'
    if zs3_file.is_file():
        try:
            with open(zs3_file, 'r', encoding='gbk', errors='ignore') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 6:
                        bname, bcode, btype, _, _, key = parts[:6]
                        bcode_full = f"{bcode}.SH" if not bcode.endswith(('.SH', '.SZ', '.BJ')) else bcode
                        zs_map[key] = (bname, bcode_full, btype)
        except Exception as exc:
            logger.debug("读取 tdxzs3.cfg 异常: %s", exc)

    # 2. Parse tdxhy.cfg: market|code6|tdx_hy|...|research_hy
    tdxhy_file = hq_cache / 'tdxhy.cfg'
    if tdxhy_file.is_file():
        try:
            with open(tdxhy_file, 'r', encoding='gbk', errors='ignore') as f:
                for line in f:
                    parts = line.strip().split('|')
                    if len(parts) >= 3 and parts[0] == market_prefix and parts[1] == code6:
                        tdx_hy = parts[2]
                        research_hy = parts[5] if len(parts) > 5 else ''

                        if tdx_hy and tdx_hy in zs_map:
                            bname, bcode, _ = zs_map[tdx_hy]
                            key = (bcode, 'industry')
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append({
                                    'BlockCode': bcode,
                                    'BlockName': bname,
                                    'BlockType': 'industry',
                                    'Count': None,
                                    'source': 'local_tdx_cache',
                                })

                        if research_hy and research_hy in zs_map:
                            bname, bcode, _ = zs_map[research_hy]
                            key = (bcode, 'research_industry')
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append({
                                    'BlockCode': bcode,
                                    'BlockName': bname,
                                    'BlockType': 'research_industry',
                                    'Count': None,
                                    'source': 'local_tdx_cache',
                                })
                        break
        except Exception as exc:
            logger.debug("读取 tdxhy.cfg 异常: %s", exc)

    # 3. Parse infoharbor_block.dat: #GN_<name>,<count>,<code88>,... / #FG_... / #ZS_...
    infoharbor_file = hq_cache / 'infoharbor_block.dat'
    if infoharbor_file.is_file():
        type_prefix_map = {
            '#GN': 'concept',
            '#FG': 'style',
            '#ZS': 'index',
        }
        try:
            current_block: tuple[str, str, str, int | None] | None = None
            with open(infoharbor_file, 'r', encoding='gbk', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('#'):
                        current_block = None
                        prefix = line[:3]
                        if prefix in type_prefix_map:
                            btype = type_prefix_map[prefix]
                            rest = line[4:] if len(line) > 4 and line[3] == '_' else line[3:]
                            hparts = rest.split(',')
                            bname = hparts[0].strip()
                            count = int(hparts[1]) if len(hparts) > 1 and hparts[1].isdigit() else None
                            bcode = hparts[2].strip() if len(hparts) > 2 and hparts[2].strip() else ''
                            if bcode and not bcode.endswith(('.SH', '.SZ', '.BJ')):
                                bcode = f"{bcode}.SH"
                            current_block = (bcode, bname, btype, count)
                    elif current_block is not None:
                        members = line.split(',')
                        if target_token in members:
                            bcode, bname, btype, count = current_block
                            key = (bcode or bname, btype)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append({
                                    'BlockCode': bcode,
                                    'BlockName': bname,
                                    'BlockType': btype,
                                    'Count': count,
                                    'source': 'local_tdx_cache',
                                })
        except Exception as exc:
            logger.debug("读取 infoharbor_block.dat 异常: %s", exc)

    return results


def parse_downloaded_file(
    tdx_path: str,
    symbol: str,
    down_type: int,
    down_date: str = '',
) -> dict[str, Any]:
    """Parse downloaded data files (top10 shareholders, ETF redemption list) in PYPlugins/data."""
    valid_symbol = stock_code(symbol)
    code6 = valid_symbol.split('.')[0]

    plugins_data = Path(tdx_path).resolve() / "PYPlugins" / "data"
    if not plugins_data.is_dir():
        raise TdxDataError(f"未找到通达信下载数据目录: {plugins_data}")

    if down_type == 1:
        # 十大股东: gp{code6}_{year}.json
        target_file = None
        if down_date:
            year = down_date[:4]
            candidate = plugins_data / f"gp{code6}_{year}.json"
            if candidate.is_file():
                target_file = candidate

        if target_file is None:
            candidates = sorted(plugins_data.glob(f"gp{code6}_*.json"), key=lambda p: p.name, reverse=True)
            if candidates:
                target_file = candidates[0]

        if target_file is None:
            raise TdxDataError(f"未找到股票 {symbol} 的十大股东下载数据文件 (gp{code6}_*.json)")

        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                outer_data = json.load(f)
        except Exception as exc:
            raise TdxDataError(f"解析十大股东文件 {target_file} 失败: {exc}") from exc

        reports: dict[str, dict[str, Any]] = {}
        if isinstance(outer_data, list):
            for item in outer_data:
                if not isinstance(item, dict):
                    continue
                raw_inner = item.get('gdxx', item)
                if isinstance(raw_inner, str):
                    try:
                        inner = json.loads(raw_inner)
                    except Exception:
                        continue
                elif isinstance(raw_inner, dict):
                    inner = raw_inner
                else:
                    continue

                if isinstance(inner, dict):
                    for report_date, info in inner.items():
                        if not isinstance(info, dict):
                            continue
                        gd_list = []
                        for gd in info.get('gd', []):
                            clean_gd = dict(gd)
                            if 'pm' in clean_gd:
                                try:
                                    clean_gd['pm'] = int(clean_gd['pm'])
                                except (ValueError, TypeError):
                                    pass
                            for num_f in ('cgsl', 'cgbl'):
                                if num_f in clean_gd:
                                    try:
                                        clean_gd[num_f] = float(clean_gd[num_f])
                                    except (ValueError, TypeError):
                                        pass
                            gd_list.append(clean_gd)

                        ltgd_list = []
                        for gd in info.get('ltgd', []):
                            clean_gd = dict(gd)
                            if 'pm' in clean_gd:
                                try:
                                    clean_gd['pm'] = int(clean_gd['pm'])
                                except (ValueError, TypeError):
                                    pass
                            for num_f in ('cgsl', 'cgbl'):
                                if num_f in clean_gd:
                                    try:
                                        clean_gd[num_f] = float(clean_gd[num_f])
                                    except (ValueError, TypeError):
                                        pass
                            ltgd_list.append(clean_gd)

                        reports[str(report_date)] = {
                            'top10_shareholders': gd_list,
                            'top10_float_shareholders': ltgd_list,
                        }

        return {
            'file': str(target_file),
            'stock': valid_symbol,
            'down_type': 1,
            'reports': reports,
        }

    elif down_type == 2:
        # ETF 申赎清单
        pattern = f"*{code6}*"
        candidates = list(plugins_data.glob(pattern))
        if down_date:
            filtered = [p for p in candidates if down_date in p.name]
            if filtered:
                candidates = filtered
        if not candidates:
            raise TdxDataError(f"未找到 ETF {symbol} 的申赎清单数据文件")

        target_file = sorted(candidates, key=lambda p: p.name, reverse=True)[0]
        try:
            if target_file.suffix.lower() == '.json':
                with open(target_file, 'r', encoding='utf-8') as f:
                    content = json.load(f)
            else:
                with open(target_file, 'r', encoding='gbk', errors='ignore') as f:
                    content = f.read().splitlines()
        except Exception as exc:
            raise TdxDataError(f"解析 ETF 申赎文件 {target_file} 失败: {exc}") from exc

        return {
            'file': str(target_file),
            'stock': valid_symbol,
            'down_type': 2,
            'content': content,
        }
    else:
        raise TdxParamError(f"不支持的下载类型 {down_type}；仅支持 1 (十大股东) 或 2 (ETF申赎清单)")


def detect_running_tdx_clients(timeout: float = 2.0) -> list[str]:
    """Best-effort Windows process detector returning install paths of running tdxw.exe instances.

    Never raises; returns empty list if tdxw is not running or if platform/command fails.
    """
    if os.name != 'nt':
        return []

    cmd = [
        'powershell',
        '-NoProfile',
        '-Command',
        'Get-Process tdxw -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path',
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            return []
        paths: list[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line and line.lower().endswith('.exe'):
                parent_dir = str(Path(line).parent.resolve())
                if parent_dir not in paths:
                    paths.append(parent_dir)
        return paths
    except Exception as exc:
        logger.debug("检测通达信运行进程异常: %s", exc)
        return []
