"""Read-only CLI probes for README examples; never dispatch client/sector writes or trades.

Run with a logged-in TQ-capable client. Each child owns its session and has a
bounded timeout. JSON on stdout is a test report, not market data for trading.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


CASES = [
    ("local", "version", ["--version"], 0),
    ("local", "help", ["--help"], 0),
    ("local", "status", ["status", "--json"], 0),
    ("local", "config_show", ["config", "show", "--json"], 0),
    ("local", "order_preview", ["trade", "order", "-s", "600519.SH", "-t", "buy", "-v", "100", "-p", "10", "--json"], 0),
    ("local", "article_zdt_missing_stocks", ["market", "zdt", "--json"], 2),
    ("local", "article_pool_missing_code", ["sector", "create", "-n", "AI突破选股", "--json"], 2),
    ("local", "article_core_pool_missing_code", ["sector", "create", "-n", "核心资产池", "--json"], 2),
    ("local", "article_pool_reversed_options", ["sector", "update-stocks", "-s", "AI突破选股", "-c", "600519.SH,000001.SZ,600487.SH", "--json"], 2),
    ("local", "article_core_pool_reversed_options", ["sector", "update-stocks", "-s", "核心资产池", "-c", "600519.SH,601318.SH", "--json"], 2),
    ("local", "article_trading_invalid_option", ["financial", "trading", "-s", "600519.SH", "--json"], 2),
    ("market", "init", ["init", "--json"], 0),
    ("market", "close", ["close", "--json"], 0),
    ("market", "article_kline", ["market", "get", "-s", "600519.SH,000001.SZ", "-p", "1d", "-c", "60", "-d", "front", "--json"], 0),
    ("market", "article_snapshot", ["market", "snapshot", "-s", "600487.SH", "--json"], 0),
    ("market", "corrected_zdt", ["market", "zdt", "-s", "600519.SH,000001.SZ", "--json"], 0),
    ("market", "article_divid", ["market", "divid", "-s", "600487.SH", "--start", "20230101", "--end", "20241231", "--json"], 0),
    ("market", "exday", ["market", "exday", "-s", "600519.SH", "-c", "2", "--json"], 0),
    ("market", "pv", ["market", "pv", "-s", "600519.SH", "--json"], 0),
    ("stock", "info", ["stock", "info", "-s", "600487.SH", "--json"], 0),
    ("stock", "more", ["stock", "more", "-s", "600519.SH", "-f", "HqDate,ZTPrice,DTPrice,FCAmo,EverZTCount,DynaPE,PB_MRQ,Zsz,Ltsz", "--json"], 0),
    ("stock", "list_default", ["stock", "list", "--json"], 0),
    ("stock", "list_all_a", ["stock", "list", "-m", "5", "--json"], 0),
    ("stock", "calendar", ["stock", "calendar", "-c", "5", "--json"], 0),
    ("stock", "article_search", ["stock", "search", "-k", "亨通", "--json"], 0),
    ("stock", "article_gb", ["stock", "gb", "-s", "600519.SH", "--json"], 0),
    ("stock", "article_kzz", ["stock", "kzz", "-s", "113031.SH", "--json"], 0),
    ("stock", "active_kzz", ["stock", "kzz", "-s", "113042.SH", "--json"], 0),
    ("sector", "article_sector_list", ["sector", "list", "-t", "0", "--json"], 0),
    ("sector", "article_sector_stocks", ["sector", "stocks", "-s", "半导体", "--json"], 0),
    ("sector", "sector_user", ["sector", "user", "--json"], 0),
    ("financial", "article_financial", ["financial", "get", "-s", "600487.SH", "-f", "FN1,FN4,FN6", "--start", "20240101", "--end", "20241231", "--json"], 0),
    ("financial", "by_date", ["financial", "by-date", "-s", "600487.SH", "-f", "FN1,FN4,FN6", "-y", "2024", "-m", "1231", "--json"], 0),
    ("financial", "corrected_trading", ["financial", "trading", "--type", "stock", "--target", "600519.SH", "-f", "GP1", "--start", "20261020", "--end", "20261020", "--json"], 0),
    ("formula", "article_formula_list", ["formula", "list", "-t", "0", "--json"], 0),
    ("formula", "formula_info", ["formula", "info", "-c", "MA", "-t", "0", "--json"], 0),
    ("formula", "article_formula_zb", ["formula", "zb", "-n", "MA", "-a", "5,10,20", "-s", "600519.SH,000001.SZ", "-c", "60", "--json"], 0),
    ("formula", "article_formula_xg", ["formula", "xg", "-n", "KDJ", "-a", "9,3,3", "-s", "600519.SH,000001.SZ", "--json"], 0),
    ("formula", "selection_formula_list", ["formula", "list", "-t", "1", "--json"], 0),
    ("formula", "selection_formula_info", ["formula", "info", "-c", "UPN", "-t", "1", "--json"], 0),
    ("formula", "corrected_formula_xg", ["formula", "xg", "-n", "UPN", "-a", "3", "-s", "600519.SH,000001.SZ", "-c", "60", "--json"], 0),
]


def summarize(value):
    if isinstance(value, dict):
        result = {"type": "object", "items": len(value), "keys": list(value)[:12]}
        if value.get("success") is False:
            result.update({key: value.get(key) for key in ("error_code", "error", "details")})
        for key in ("run_id", "scope", "session_initialized", "HqDate", "ZTPrice", "DTPrice", "FCAmo", "EverZTCount", "DynaPE", "PB_MRQ", "Zsz", "Ltsz"):
            if key in value:
                result[key] = value[key]
        if value and isinstance(next(iter(value.values())), (dict, list)):
            first = next(iter(value.values()))
            result["first_item_fields_or_length"] = list(first)[:16] if isinstance(first, dict) else len(first)
        return result
    if isinstance(value, list):
        result = {"type": "array", "items": len(value)}
        if value and isinstance(value[0], dict):
            result["first_item_fields"] = list(value[0])[:16]
        return result
    return {"type": type(value).__name__}


def probe(case, timeout):
    group, name, args, expected = case
    started = time.monotonic()
    record = {"group": group, "case": name, "args": args, "expected_exit": expected}
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "cli_anything.tdx_tq.tdx_tq_cli", *args],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
        record.update(exit_code=result.returncode, stderr=result.stderr.strip()[:2000])
        if "--json" in args:
            try:
                value = json.loads(result.stdout, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                record["json_valid"] = True
                record["summary"] = summarize(value)
                if expected == 0 and group != "local" and name not in {"init", "close"}:
                    record["nonempty_result"] = bool(value) and not (isinstance(value, dict) and value.get("success") is False)
            except ValueError:
                record.update(json_valid=False, stdout_excerpt=result.stdout[:1000])
        else:
            record["text"] = result.stdout[:500]
        record["matched_expectation"] = (result.returncode == expected
                                         and record.get("json_valid", True)
                                         and record.get("nonempty_result", True))
    except subprocess.TimeoutExpired:
        record.update(timed_out=True, matched_expectation=False)
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=sorted({c[0] for c in CASES}))
    parser.add_argument("--case", action="append", choices=[c[1] for c in CASES], help="Select one or more named probes")
    parser.add_argument("--timeout", type=float, default=45)
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    results = []
    for case in CASES:
        if (options.group is None or case[0] == options.group) and (not options.case or case[1] in options.case):
            result = probe(case, options.timeout)
            results.append(result)
            print(f"{result['case']}: exit={result.get('exit_code', 'timeout')} matched={result['matched_expectation']}", file=sys.stderr, flush=True)
    print(json.dumps({"schema_version": 1, "scope": "read_only_probes", "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["matched_expectation"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
