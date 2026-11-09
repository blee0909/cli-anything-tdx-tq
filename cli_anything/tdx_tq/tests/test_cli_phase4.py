"""Comprehensive tests for Phase 4: TDX local formula engine, formula eval closed-loop,
--local offline flags on formula list/info/zb/xg/exp, and daemon action classification.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.daemon import classify_cli_action
from cli_anything.tdx_tq.core.errors import (
    TdxDataError,
    TdxParamError,
    TdxUnsupportedCapabilityError,
)


class MockPhase4SDK:
    """Mock TDX SDK supporting multi-stock K-lines, sectors, markets, and client UI pushes."""

    def __init__(self):
        self.ensure_initialized_called = 0
        self.user_blocks = []
        self.panels = []
        self.warns = []
        self.sector_members = {
            '半导体': ['600519.SH', '000001.SZ'],
            'EMPTY_BK': [],
        }
        self.market_members = {
            '0': ['600519.SH', '000001.SZ'],
            'EMPTY_MKT': [],
        }
        self.formula_all_result = [{'acCode': 'MA'}]
        self.fail_formula_get_all = False

    def formula_get_all(self, formula_type: int = 0):
        if self.fail_formula_get_all:
            raise TdxUnsupportedCapabilityError("模拟通达信不支持原生 C 公式列表")
        return self.formula_all_result

    def formula_get_info(self, formula_type: int = 0, formula_code: str = ''):
        return {'acCode': formula_code, 'name': formula_code}

    def formula_zb(self, **kwargs):
        return {'result': 1}

    def formula_process_mul_zb(self, **kwargs):
        return {'result': 1}

    def formula_xg(self, **kwargs):
        return ['600519.SH']

    def formula_process_mul_xg(self, **kwargs):
        return ['600519.SH']

    def formula_exp(self, **kwargs):
        return {'result': 1}

    def formula_process_mul_exp(self, **kwargs):
        return {'result': 1}

    def get_stock_list_in_sector(self, block_code: str = '', block_type: int = 0, list_type: int = 0):
        return self.sector_members.get(block_code, [])

    def get_stock_list(self, market: str = '', list_type: int = 0):
        return self.market_members.get(market, [])

    def send_user_block(self, block_code: str = '', stock_list: list[str] | None = None, show: bool = False):
        self.user_blocks.append({
            'block_code': block_code,
            'stock_list': list(stock_list or []),
            'show': show,
        })
        return {'ErrorId': '0', 'Count': len(stock_list or [])}

    def print_to_tdx(self, **kwargs):
        self.panels.append(kwargs)
        return {'ErrorId': '0'}

    def send_warn(self, **kwargs):
        self.warns.append(kwargs)
        return {'ErrorId': '0'}

    def get_market_data(
        self,
        stock_list: list[str],
        dividend_type: str = 'front',
        period: str = '1d',
        count: int = 60,
        start_time: str = '',
        end_time: str = '',
        fill_data: bool = False,
    ):
        dates = ['20261101', '20261102', '20261103', '20261104', '20261105']
        # 600519.SH crosses MA(C,2) > MA(C,3) on last bar (20261105) and V=100 > 50
        # 000001.SZ stays flat with V=10 < 50
        data = {
            'close': pd.DataFrame({
                '600519.SH': [10.0, 10.0, 9.8, 9.8, 12.0],
                '000001.SZ': [10.0, 10.0, 10.0, 10.0, 10.0],
            }, index=dates),
            'open': pd.DataFrame({
                '600519.SH': [9.9, 9.9, 9.7, 9.7, 10.0],
                '000001.SZ': [10.0, 10.0, 10.0, 10.0, 10.0],
            }, index=dates),
            'high': pd.DataFrame({
                '600519.SH': [10.1, 10.1, 9.9, 9.9, 12.2],
                '000001.SZ': [10.1, 10.1, 10.1, 10.1, 10.1],
            }, index=dates),
            'low': pd.DataFrame({
                '600519.SH': [9.8, 9.8, 9.6, 9.6, 9.9],
                '000001.SZ': [9.9, 9.9, 9.9, 9.9, 9.9],
            }, index=dates),
            'volume': pd.DataFrame({
                '600519.SH': [20.0, 20.0, 30.0, 30.0, 100.0],
                '000001.SZ': [10.0, 10.0, 10.0, 10.0, 10.0],
            }, index=dates),
            'amount': pd.DataFrame({
                '600519.SH': [200.0, 200.0, 294.0, 294.0, 1200.0],
                '000001.SZ': [100.0, 100.0, 100.0, 100.0, 100.0],
            }, index=dates),
        }
        res: dict[str, pd.DataFrame] = {}
        for k, df in data.items():
            cols = [s for s in stock_list if s in df.columns]
            res[k] = df[cols] if cols else pd.DataFrame(index=dates)
        return res


class FakeSession:
    def __init__(self, raw: MockPhase4SDK):
        self.raw = raw
        self.tdx = raw
        self.is_initialized = False

    def ensure_initialized(self):
        self.is_initialized = True
        self.raw.ensure_initialized_called += 1


@pytest.fixture
def mock_phase4_session(monkeypatch):
    sdk = MockPhase4SDK()
    sess = FakeSession(sdk)
    monkeypatch.setattr(cli, 'session', sess)
    return sess


# =====================================================================
# 1. Daemon classify_cli_action tests
# =====================================================================

def test_daemon_classify_cli_action_formula():
    # local_or_bypass
    assert classify_cli_action(['formula', 'list', '--local']) == 'local_or_bypass'
    assert classify_cli_action(['formula', 'list', '--local', '--json']) == 'local_or_bypass'
    assert classify_cli_action(['formula', 'info', '-c', 'MACD', '--local']) == 'local_or_bypass'

    # formula eval read vs op_mutation
    assert classify_cli_action(['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH']) == 'read'
    assert classify_cli_action(['formula', 'eval', '-n', 'MACD', '-s', '600519.SH']) == 'read'

    # op_mutation via push flags
    assert classify_cli_action(['formula', 'eval', '--push-temp']) == 'op_mutation'
    assert classify_cli_action(['formula', 'eval', '--push-sector', 'AIPOOL']) == 'op_mutation'
    assert classify_cli_action(['formula', 'eval', '--push-sector=AIPOOL']) == 'op_mutation'
    assert classify_cli_action(['formula', 'eval', '--push-panel', 'pad1']) == 'op_mutation'
    assert classify_cli_action(['formula', 'eval', '--push-panel=pad1']) == 'op_mutation'
    assert classify_cli_action(['formula', 'eval', '--push-warn']) == 'op_mutation'

    # standard formula read subcommands
    assert classify_cli_action(['formula', 'zb', '-n', 'MA']) == 'read'
    assert classify_cli_action(['formula', 'xg', '-n', 'MA']) == 'read'
    assert classify_cli_action(['formula', 'exp', '-n', 'BBI']) == 'read'


# =====================================================================
# 2. formula list / info --local tests (offline, no SDK)
# =====================================================================

def test_formula_list_local_offline(mock_phase4_session):
    # JSON mode (default type=0)
    res_json = CliRunner().invoke(cli.cli, ['formula', 'list', '--local', '--json'])
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert isinstance(data, list)
    assert any(f['acCode'] == 'MACD' for f in data)
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # Text mode & formula_type=1 (selection formulas)
    res_text = CliRunner().invoke(cli.cli, ['formula', 'list', '--local', '-t', '1'])
    assert res_text.exit_code == 0, res_text.output
    assert "MA_CROSS" in res_text.stdout
    assert mock_phase4_session.raw.ensure_initialized_called == 0


def test_formula_list_sdk_fallback_error(mock_phase4_session):
    mock_phase4_session.raw.fail_formula_get_all = True
    res = CliRunner().invoke(cli.cli, ['formula', 'list', '--json'])
    assert res.exit_code != 0
    err = json.loads(res.stdout)
    assert err['success'] is False
    assert "--local" in err['error'] or "formula eval" in err['error']


def test_formula_info_local_offline(mock_phase4_session):
    # Success JSON
    res = CliRunner().invoke(cli.cli, ['formula', 'info', '-c', 'MACD', '--local', '--json'])
    assert res.exit_code == 0, res.output
    info = json.loads(res.stdout)
    assert info['acCode'] == 'MACD'
    assert info['engine'] == 'local_vectorized'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # Success Text
    res_text = CliRunner().invoke(cli.cli, ['formula', 'info', '-c', 'KDJ', '--local'])
    assert res_text.exit_code == 0, res_text.output
    assert "KDJ" in res_text.stdout
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # Empty code error
    res_empty = CliRunner().invoke(cli.cli, ['formula', 'info', '-c', '  ', '--local', '--json'])
    assert res_empty.exit_code == 2
    assert json.loads(res_empty.stdout)['error_code'] == 'INVALID_PARAMETER'

    # Non-existent code error
    res_missing = CliRunner().invoke(cli.cli, ['formula', 'info', '-c', 'NO_SUCH_CODE', '--local', '--json'])
    assert res_missing.exit_code == 2
    assert json.loads(res_missing.stdout)['error_code'] == 'INVALID_PARAMETER'


# =====================================================================
# 3. formula zb / xg / exp --local tests
# =====================================================================

def test_formula_subcommands_local_validation(mock_phase4_session):
    # Missing -s must fail BEFORE ensure_initialized
    for sub in ('zb', 'xg', 'exp'):
        res = CliRunner().invoke(cli.cli, ['formula', sub, '-n', 'MA', '--local', '--json'])
        assert res.exit_code == 2, res.output
        err = json.loads(res.stdout)
        assert err['error_code'] == 'INVALID_PARAMETER'
        assert mock_phase4_session.raw.ensure_initialized_called == 0

    # Unknown formula name must fail BEFORE ensure_initialized
    res_bad = CliRunner().invoke(cli.cli, ['formula', 'zb', '-n', 'NON_EXISTENT', '-s', '600519.SH', '--local', '--json'])
    assert res_bad.exit_code == 2
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # Empty formula name must fail BEFORE ensure_initialized
    res_name = CliRunner().invoke(cli.cli, ['formula', 'zb', '-n', '   ', '-s', '600519.SH', '--local', '--json'])
    assert res_name.exit_code == 2
    assert mock_phase4_session.raw.ensure_initialized_called == 0


def test_formula_zb_local_execution(mock_phase4_session):
    # JSON mode with allow_partial
    res = CliRunner().invoke(cli.cli, [
        'formula', 'zb', '-n', 'MA', '-s', '600519.SH,000001.SZ', '--local', '--json',
        '--args', 'N1=2,N2=3,N3=4,N4=5', '--xsflag', '2', '--allow-partial',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['formula'] == 'MA'
    assert out['total_evaluated'] == 2
    assert '600519.SH' in out['results']
    assert '000001.SZ' in out['results']
    assert 'completeness' in out
    assert mock_phase4_session.raw.ensure_initialized_called == 1

    # Text mode
    res_text = CliRunner().invoke(cli.cli, [
        'formula', 'zb', '-n', 'MA', '-s', '600519.SH', '--local',
    ])
    assert res_text.exit_code == 0, res_text.output
    assert "MA" in res_text.stdout


def test_formula_xg_local_execution(mock_phase4_session):
    res = CliRunner().invoke(cli.cli, [
        'formula', 'xg', '-n', 'MA_CROSS', '-s', '600519.SH,000001.SZ', '--local', '--json',
        '--args', 'N1=2,N2=3',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['formula'] == 'MA_CROSS'
    assert out['selected_count'] == 1
    assert out['selected_stocks'] == ['600519.SH']

    # Text mode
    res_text = CliRunner().invoke(cli.cli, [
        'formula', 'xg', '-n', 'MA_CROSS', '-s', '600519.SH,000001.SZ', '--local',
        '--args', 'N1=2,N2=3',
    ])
    assert res_text.exit_code == 0, res_text.output
    assert "选股命中" in res_text.stdout


def test_formula_exp_local_execution(mock_phase4_session):
    res = CliRunner().invoke(cli.cli, [
        'formula', 'exp', '-n', 'BBI', '-s', '600519.SH', '--local', '--json',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['formula'] == 'BBI'
    assert '600519.SH' in out['results']


# =====================================================================
# 4. formula eval: Pre-connection Rejection Tests
# =====================================================================

def test_formula_eval_pre_connection_failures(mock_phase4_session, tmp_path):
    runner = CliRunner()

    # 1. Invalid syntax / unsupported function
    res = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'BAD_FUNC(C)', '-s', '600519.SH', '--json'])
    assert res.exit_code == 2, res.output
    assert json.loads(res.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 2. Function argument count mismatch
    res_args = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C)', '-s', '600519.SH', '--json'])
    assert res_args.exit_code == 2
    assert json.loads(res_args.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 3. Custom expr with positional args instead of named
    res_pos = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, N)', '-s', '600519.SH', '-a', '5', '--json'])
    assert res_pos.exit_code == 2
    assert json.loads(res_pos.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 4. Reserved name conflict in args
    res_resv = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '-a', 'C=10', '--json'])
    assert res_resv.exit_code == 2
    assert json.loads(res_resv.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 5. Missing formula file
    res_f = runner.invoke(cli.cli, ['formula', 'eval', '--formula-file', str(tmp_path / 'missing.tdx'), '-s', '600519.SH', '--json'])
    assert res_f.exit_code == 2
    assert json.loads(res_f.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 6. Mutual exclusion: expr + name
    res_mut = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-n', 'MACD', '-s', '600519.SH', '--json'])
    assert res_mut.exit_code == 2
    assert json.loads(res_mut.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 7. Mutual exclusion: stocks + sector
    res_u = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '--sector', '半导体', '--json'])
    assert res_u.exit_code == 2
    assert json.loads(res_u.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 8. Mutual exclusion: push_sector + push_temp
    res_push = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '--push-sector', 'S1', '--push-temp', '--json'])
    assert res_push.exit_code == 2
    assert json.loads(res_push.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 8b. Empty push_sector name
    res_sec_empty = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '--push-sector', '   ', '--json'])
    assert res_sec_empty.exit_code == 2
    assert json.loads(res_sec_empty.stdout)['error_code'] == 'INVALID_PARAMETER'

    # 8c. Non-numeric value in args
    res_bad_val = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, N)', '-s', '600519.SH', '-a', 'N=abc', '--json'])
    assert res_bad_val.exit_code == 2
    assert json.loads(res_bad_val.stdout)['error_code'] == 'INVALID_PARAMETER'

    # 8d. Unary operator with bad function inside
    res_unary = runner.invoke(cli.cli, ['formula', 'eval', '-e', '-BAD_FUNC(C)', '-s', '600519.SH', '--json'])
    assert res_unary.exit_code == 2
    assert json.loads(res_unary.stdout)['error_code'] == 'INVALID_PARAMETER'

    # 9. Mutual exclusion: start + count
    res_date = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '--start', '20261101', '-c', '10', '--json'])
    assert res_date.exit_code == 2
    assert json.loads(res_date.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0

    # 10. Invalid export extension
    res_exp = runner.invoke(cli.cli, ['formula', 'eval', '-e', 'MA(C, 5)', '-s', '600519.SH', '-o', 'out.badext', '--json'])
    assert res_exp.exit_code == 2
    assert json.loads(res_exp.stdout)['error_code'] == 'INVALID_PARAMETER'
    assert mock_phase4_session.raw.ensure_initialized_called == 0


# =====================================================================
# 5. formula eval: Full Quant Closed-Loop Workflow Tests
# =====================================================================

def test_formula_eval_custom_expr_with_selection(mock_phase4_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-e', 'CROSS(MA(C,2), MA(C,3)) AND V > 50',
        '-s', '600519.SH,000001.SZ',
        '--select',
        '--json',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['total_evaluated'] == 2
    assert out['selected_count'] == 1
    assert out['selected_stocks'] == ['600519.SH']
    assert len(out['summary_rows']) == 2
    # Verify summary row has SELECT output and hit flag
    row600519 = next(r for r in out['summary_rows'] if r['stock'] == '600519.SH')
    assert row600519['hit'] is True
    assert row600519['SELECT'] == 1.0


def test_formula_eval_formula_file_with_comments_and_assign(mock_phase4_session, tmp_path):
    runner = CliRunner()
    formula_file = tmp_path / "custom_strategy.tdx"
    formula_file.write_text(
        "{\n"
        "  这是一个多行注释测试\n"
        "}\n"
        "MA2 := MA(CLOSE, 2);\n"
        "MA3 := MA(CLOSE, 3);\n"
        "BUY_COND: CROSS(MA2, MA3) AND VOL > 50;\n",
        encoding="utf-8",
    )
    res = runner.invoke(cli.cli, [
        'formula', 'eval',
        '--formula-file', str(formula_file),
        '-s', '600519.SH,000001.SZ',
        '--select',
        '--json',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['selected_count'] == 1
    assert out['selected_stocks'] == ['600519.SH']
    row = next(r for r in out['summary_rows'] if r['stock'] == '600519.SH')
    assert row['BUY_COND'] == 1.0

    # GBK-encoded formula file fallback
    gbk_file = tmp_path / "gbk_strategy.tdx"
    gbk_file.write_bytes("DIFF := EMA(C, 12); BUY_SIG: DIFF > 0;".encode('gbk'))
    res_gbk = runner.invoke(cli.cli, [
        'formula', 'eval',
        '--formula-file', str(gbk_file),
        '-s', '600519.SH',
        '--json',
    ])
    assert res_gbk.exit_code == 0, res_gbk.output
    out_gbk = json.loads(res_gbk.stdout)
    assert out_gbk['summary_rows'][0]['BUY_SIG'] == 1.0


def test_formula_eval_builtin_formulas(mock_phase4_session):
    runner = CliRunner()

    # Technical indicator (ftype=0)
    res_macd = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MACD',
        '-t', '0',
        '-s', '600519.SH',
        '--json',
    ])
    assert res_macd.exit_code == 0, res_macd.output
    out = json.loads(res_macd.stdout)
    assert out['formula'] == 'MACD'
    assert 'DIF' in out['summary_rows'][0]

    # Expert system formula (ftype=2)
    res_exp = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MACD',
        '-t', '2',
        '-s', '600519.SH',
        '--json',
    ])
    assert res_exp.exit_code == 0, res_exp.output
    out_exp = json.loads(res_exp.stdout)
    assert 'ENTERLONG' in out_exp['summary_rows'][0]

    # Selection formula (ftype=1, auto-enables effective_select)
    res_cross = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MA_CROSS',
        '-s', '600519.SH,000001.SZ',
        '-a', 'N1=2,N2=3',
        '--json',
    ])
    assert res_cross.exit_code == 0, res_cross.output
    out_cross = json.loads(res_cross.stdout)
    assert out_cross['selected_stocks'] == ['600519.SH']


def test_formula_eval_universe_modes(mock_phase4_session):
    runner = CliRunner()

    # --sector
    res_sector = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MA_CROSS',
        '--sector', '半导体',
        '--block-type', '0',
        '-a', 'N1=2,N2=3',
        '--json',
    ])
    assert res_sector.exit_code == 0, res_sector.output
    assert json.loads(res_sector.stdout)['total_evaluated'] == 2

    # --market-list
    res_mkt = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MA_CROSS',
        '--market-list', '0',
        '-a', 'N1=2,N2=3',
        '--json',
    ])
    assert res_mkt.exit_code == 0, res_mkt.output
    assert json.loads(res_mkt.stdout)['total_evaluated'] == 2

    # Empty sector error
    res_empty_sec = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MA_CROSS',
        '--sector', 'EMPTY_BK',
        '--json',
    ])
    assert res_empty_sec.exit_code != 0
    assert json.loads(res_empty_sec.stdout)['error_type'] == 'TdxDataError'

    # Empty market list error
    res_empty_mkt = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-n', 'MA_CROSS',
        '--market-list', 'EMPTY_MKT',
        '--json',
    ])
    assert res_empty_mkt.exit_code != 0
    assert json.loads(res_empty_mkt.stdout)['error_type'] == 'TdxDataError'


def test_formula_eval_closed_loop_push_and_export(mock_phase4_session, tmp_path):
    runner = CliRunner()
    out_jsonl = tmp_path / "results.jsonl"

    # Positive hits: push temp, panel, warn, export
    res = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-e', 'CROSS(MA(C,2), MA(C,3)) AND V > 50',
        '-s', '600519.SH,000001.SZ',
        '--push-temp',
        '--push-panel', 'QUANT_PAD',
        '--push-warn',
        '--warn-reason', '测试金叉预警',
        '-o', str(out_jsonl),
        '--allow-partial',
        '--json',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)

    # 1. Check sector push (temp)
    assert out['actions']['sector_push']['pushed'] is True
    assert out['actions']['sector_push']['block_code'] == ''
    assert out['actions']['sector_push']['temp'] is True
    assert out['actions']['sector_push']['count'] == 1
    assert len(mock_phase4_session.raw.user_blocks) == 1
    assert mock_phase4_session.raw.user_blocks[0]['stock_list'] == ['600519.SH']

    # 2. Check panel push
    assert out['actions']['panel_push']['group_count'] == 1
    assert 'sp_path' in out['actions']['panel_push']
    assert len(mock_phase4_session.raw.panels) == 1

    # 3. Check warn push
    assert out['actions']['warn_push']['count'] == 1
    assert out['actions']['warn_push']['stocks'] == ['600519.SH']
    assert len(mock_phase4_session.raw.warns) == 1

    # 4. Check export
    assert out['actions']['export']['records'] == 2
    assert out_jsonl.is_file()

    # 5. Check completeness envelope
    assert 'completeness' in out


def test_formula_eval_closed_loop_push_sector_zero_hits_safeguard(mock_phase4_session):
    runner = CliRunner()
    # Strategy that will yield 0 hits
    res = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-e', 'CLOSE > 99999',
        '-s', '600519.SH,000001.SZ',
        '--push-sector', 'AIPOOL',
        '--push-warn',
        '--json',
    ])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out['selected_count'] == 0

    # Sector push safeguard: block must NOT be wiped!
    assert out['actions']['sector_push']['pushed'] is False
    assert out['actions']['sector_push']['reason'] == 'no_selected_stocks'
    assert len(mock_phase4_session.raw.user_blocks) == 0

    # Warn push safeguard:
    assert out['actions']['warn_push']['pushed'] is False
    assert out['actions']['warn_push']['reason'] == 'no_selected_stocks'
    assert len(mock_phase4_session.raw.warns) == 0


def test_formula_eval_text_output_with_actions(mock_phase4_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'formula', 'eval',
        '-e', 'CROSS(MA(C,2), MA(C,3)) AND V > 50',
        '-s', '600519.SH,000001.SZ',
        '--push-temp',
    ])
    assert res.exit_code == 0, res.output
    assert "=== 本地公式评测" in res.stdout
    assert "选股命中" in res.stdout
    assert "执行操作动作" in res.stdout
