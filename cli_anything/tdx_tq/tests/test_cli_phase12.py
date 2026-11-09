"""Comprehensive tests for Phase 1 and Phase 2 CLI capabilities.

Covers:
- status (session_state, running_clients, path_mismatch)
- config detect (offline, --apply, json/text, error when no path)
- market get --pivot & --fill (json, text, export, allow-partial, error branches)
- market watch (--jsonl, --json, text, max-events, validation branches)
- stock list --categories (offline, json, text)
- stock search (dual-path SDK or local cache, --local, fallback, empty keyword)
- stock relation (dual-path SDK or local cache, --local, fallback)
- sector update-stocks (--temp, --clear, --no-show, validation branches)
- client warn (batch, broadcast, bs-flag mapping, validation)
- client file (missing file, staging, json, text)
- client bt (json/semicolon series, count, validation, json, text)
- client panel (csv/json/stdin inputs, layout, titles, heights, json, text)
- client download (--parse, --parse-only, mutual exclusion)
- trade order (--account, preview, confirm, strategy-name, remark, validation)
"""
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.config import config
from cli_anything.tdx_tq.tests.test_contracts import Session


@pytest.fixture
def fake_session(monkeypatch):
    sess = Session()
    monkeypatch.setattr(cli, 'session', sess)
    return sess


# ==================== status tests ====================

def test_status_fields_offline(monkeypatch):
    monkeypatch.setattr(cli, 'detect_running_tdx_clients', lambda: [r'D:\tdx'])
    runner = CliRunner()

    # JSON mode
    res_json = runner.invoke(cli.cli, ['status', '--json'])
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert 'session_initialized' in data
    assert 'session_state' in data
    assert 'running_clients' in data
    assert data['running_clients'] == [r'D:\tdx']
    assert 'path_mismatch' in data
    assert 'scope' in data
    assert 'config' in data
    assert 'sdk' in data

    # Text mode
    res_text = runner.invoke(cli.cli, ['status'])
    assert res_text.exit_code == 0, res_text.output
    assert 'session_state:' in res_text.stdout or 'session_state' in res_text.stdout


def test_status_path_mismatch(monkeypatch):
    monkeypatch.setattr(cli, 'detect_running_tdx_clients', lambda: [r'C:\other_tdx'])
    monkeypatch.setattr(config, 'tdx_path', r'D:\tdx')
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['status', '--json'])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data['path_mismatch'] is True


# ==================== config detect tests ====================

def test_config_detect_no_apply(monkeypatch):
    fake_discovered = [
        {'path': r'D:\tdx', 'plugin_type': 'sys', 'tqcenter_path': r'D:\tdx\PYPlugins\sys\tqcenter.py'},
        {'path': r'C:\new_tdx', 'plugin_type': 'user', 'tqcenter_path': r'C:\new_tdx\PYPlugins\user\tqcenter.py'},
    ]
    monkeypatch.setattr(config, 'discover_tdx_paths', lambda: fake_discovered)
    monkeypatch.setattr(cli, 'detect_running_tdx_clients', lambda: [r'C:\new_tdx'])

    runner = CliRunner()
    # JSON mode: recommended path should match running client
    res = runner.invoke(cli.cli, ['config', 'detect', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['success'] is True
    assert data['recommended_path'] == r'C:\new_tdx'
    assert data['applied'] is False

    # Text mode
    res_text = runner.invoke(cli.cli, ['config', 'detect'])
    assert res_text.exit_code == 0
    assert '自动探测' in res_text.stdout
    assert r'C:\new_tdx' in res_text.stdout


def test_config_detect_apply(tmp_path, monkeypatch):
    fake_discovered = [
        {'path': str(tmp_path / 'tdx_app'), 'plugin_type': 'sys'},
    ]
    monkeypatch.setattr(config, 'discover_tdx_paths', lambda: fake_discovered)
    monkeypatch.setattr(cli, 'detect_running_tdx_clients', lambda: [])

    # Point config file to temporary directory
    cfg_file = tmp_path / 'config.json'
    monkeypatch.setattr(config, '_config_file', cfg_file)

    runner = CliRunner()
    res = runner.invoke(cli.cli, ['config', 'detect', '--apply', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['applied'] is True
    assert data['recommended_path'] == str(tmp_path / 'tdx_app')
    assert config.tdx_path == str(tmp_path / 'tdx_app')

    # Text mode with apply
    res_text = runner.invoke(cli.cli, ['config', 'detect', '--apply'])
    assert res_text.exit_code == 0
    assert '已自动探测并保存通达信路径' in res_text.stdout


def test_config_detect_apply_none_found(monkeypatch):
    monkeypatch.setattr(config, 'discover_tdx_paths', lambda: [])
    monkeypatch.setattr(cli, 'detect_running_tdx_clients', lambda: [])
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['config', 'detect', '--apply', '--json'])
    assert res.exit_code != 0
    err = json.loads(res.stdout)
    assert err['success'] is False
    assert '未自动探测到可用的通达信' in err['error']


# ==================== market get --pivot & --fill tests ====================

def test_market_get_pivot_json_and_text(fake_session):
    runner = CliRunner()
    res_json = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '--pivot', 'close', '--json'])
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert isinstance(data, list)
    assert len(data) > 0

    res_text = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '--pivot', 'close'])
    assert res_text.exit_code == 0, res_text.output
    assert '600519.SH' in res_text.stdout


def test_market_get_pivot_fill_ffill(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '--pivot', 'close', '--fill', 'ffill', '--json'
    ])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert isinstance(data, list)


def test_market_get_fill_without_pivot_fails_pre_connection(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '--fill', 'ffill', '--json'
    ])
    assert res.exit_code == 2, res.output
    init_mock.assert_not_called()
    err = json.loads(res.stdout)
    assert "--fill 仅在指定 --pivot 时可用" in err['error']


def test_market_get_pivot_with_allow_partial(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '--pivot', 'close', '--allow-partial', '--json'
    ])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert 'wide_data' in data
    assert data['pivot'] == 'close'


def test_market_get_pivot_with_output(tmp_path, fake_session):
    runner = CliRunner()
    out_csv = tmp_path / 'wide.csv'
    res = runner.invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '--pivot', 'close', '-o', str(out_csv), '--json'
    ])
    assert res.exit_code == 0, res.output
    assert out_csv.is_file()
    data = json.loads(res.stdout)
    assert data['success'] is True

    # text output
    out_xlsx = tmp_path / 'wide.xlsx'
    res_text = runner.invoke(cli.cli, [
        'market', 'get', '-s', '600519.SH', '--pivot', 'close', '-o', str(out_xlsx)
    ])
    assert res_text.exit_code == 0
    assert '数据已成功导出至' in res_text.stdout


# ==================== market watch tests ====================

def test_market_watch_pre_connection_validations(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()

    # > 100 stocks
    many_stocks = ','.join([f"{600000 + i:06d}.SH" for i in range(101)])
    res = runner.invoke(cli.cli, ['market', 'watch', '-s', many_stocks, '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # negative duration
    res = runner.invoke(cli.cli, ['market', 'watch', '-s', '600519.SH', '--duration', '-1', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # json and jsonl conflict
    res = runner.invoke(cli.cli, ['market', 'watch', '-s', '600519.SH', '--json', '--jsonl'])
    assert res.exit_code == 2
    init_mock.assert_not_called()


def test_market_watch_jsonl_and_json(fake_session):
    runner = CliRunner()

    # jsonl mode
    res_jsonl = runner.invoke(cli.cli, [
        'market', 'watch', '-s', '600519.SH', '--max-events', '1', '--duration', '0.2', '--jsonl'
    ])
    assert res_jsonl.exit_code == 0, res_jsonl.output
    lines = [line for line in res_jsonl.stdout.splitlines() if line.strip()]
    assert len(lines) >= 1
    ev = json.loads(lines[0])
    assert ev.get('event') == 'quote_update'
    assert ev.get('stock') == '600519.SH'

    # json mode
    res_json = runner.invoke(cli.cli, [
        'market', 'watch', '-s', '600519.SH', '--max-events', '1', '--duration', '0.2', '--json'
    ])
    assert res_json.exit_code == 0, res_json.output
    events = json.loads(res_json.stdout)
    assert isinstance(events, list)
    assert len(events) >= 1

    # text mode
    res_text = runner.invoke(cli.cli, [
        'market', 'watch', '-s', '600519.SH', '--max-events', '1', '--duration', '0.2'
    ])
    assert res_text.exit_code == 0, res_text.output
    assert '600519.SH' in res_text.stdout


# ==================== stock list --categories tests ====================

def test_stock_list_categories_offline(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()

    # JSON mode
    res_json = runner.invoke(cli.cli, ['stock', 'list', '--categories', '--json'])
    assert res_json.exit_code == 0, res_json.output
    init_mock.assert_not_called()
    cats = json.loads(res_json.stdout)
    assert isinstance(cats, dict)
    assert '23' in cats
    assert cats['23'] == '沪深300'

    # Text mode
    res_text = runner.invoke(cli.cli, ['stock', 'list', '--categories'])
    assert res_text.exit_code == 0, res_text.output
    init_mock.assert_not_called()
    assert '市场/板块集合代码字典' in res_text.stdout
    assert '沪深300' in res_text.stdout


# ==================== stock search tests ====================

def test_stock_search_empty_keyword():
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['stock', 'search', '-k', '   ', '--json'])
    assert res.exit_code == 2
    err = json.loads(res.stdout)
    assert "搜索关键词不能为空" in err['error']


def test_stock_search_local(monkeypatch):
    monkeypatch.setattr(
        cli,
        'search_local_stocks',
        lambda path, kw, limit=50: [{'Code': '600519.SH', 'Name': '贵州茅台', 'source': 'local_tdx_cache'}]
    )
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['stock', 'search', '-k', '茅台', '--local', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data[0]['Code'] == '600519.SH'

    res_text = runner.invoke(cli.cli, ['stock', 'search', '-k', '茅台', '--local'])
    assert res_text.exit_code == 0
    assert '600519.SH: 贵州茅台' in res_text.stdout


def test_stock_search_fallback_when_capability_missing(fake_session, monkeypatch):
    monkeypatch.setattr(fake_session.tdx, 'has_capability', lambda method: False)
    monkeypatch.setattr(
        cli,
        'search_local_stocks',
        lambda path, kw, limit=50: [{'Code': '000001.SZ', 'Name': '平安银行', 'source': 'local_tdx_cache'}]
    )
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['stock', 'search', '-k', '平安', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data[0]['Code'] == '000001.SZ'


# ==================== stock relation tests ====================

def test_stock_relation_local(monkeypatch):
    monkeypatch.setattr(
        cli,
        'lookup_local_relations',
        lambda path, stk: [{'BlockCode': 'BK001', 'BlockName': '白酒', 'BlockType': 'industry'}]
    )
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['stock', 'relation', '-s', '600519.SH', '--local', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data[0]['BlockCode'] == 'BK001'

    res_text = runner.invoke(cli.cli, ['stock', 'relation', '-s', '600519.SH', '--local'])
    assert res_text.exit_code == 0
    assert 'BK001' in res_text.stdout


def test_stock_relation_fallback(fake_session, monkeypatch):
    monkeypatch.setattr(fake_session.tdx, 'has_capability', lambda method: False)
    monkeypatch.setattr(
        cli,
        'lookup_local_relations',
        lambda path, stk: [{'BlockCode': 'BK002', 'BlockName': '金融', 'BlockType': 'industry'}]
    )
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['stock', 'relation', '-s', '600519.SH', '--json'])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data[0]['BlockCode'] == 'BK002'


# ==================== sector update-stocks tests ====================

def test_sector_update_stocks_validation(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()

    # neither code nor temp
    res = runner.invoke(cli.cli, ['sector', 'update-stocks', '-s', '600519.SH', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # both code and temp
    res = runner.invoke(cli.cli, ['sector', 'update-stocks', '-c', 'ZXG', '--temp', '-s', '600519.SH', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # neither stocks nor clear
    res = runner.invoke(cli.cli, ['sector', 'update-stocks', '-c', 'ZXG', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # both stocks and clear
    res = runner.invoke(cli.cli, ['sector', 'update-stocks', '-c', 'ZXG', '-s', '600519.SH', '--clear', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()


def test_sector_update_stocks_temp_and_clear(fake_session):
    runner = CliRunner()

    # --temp mode
    res_temp = runner.invoke(cli.cli, [
        'sector', 'update-stocks', '--temp', '-s', '600519.SH,000001.SZ', '--no-show', '--json'
    ])
    assert res_temp.exit_code == 0, res_temp.output
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'send_user_block'
    assert kwargs['block_code'] == ''
    assert kwargs['stock_list'] == ['600519.SH', '000001.SZ']
    assert kwargs['show'] is False

    # --clear mode
    res_clear = runner.invoke(cli.cli, [
        'sector', 'update-stocks', '-c', 'MYBK', '--clear', '--json'
    ])
    assert res_clear.exit_code == 0, res_clear.output
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert kwargs['block_code'] == 'MYBK'
    assert kwargs['stock_list'] == []

    # text output
    res_text = runner.invoke(cli.cli, [
        'sector', 'update-stocks', '-c', 'MYBK', '--clear'
    ])
    assert res_text.exit_code == 0
    assert '已清空' in res_text.stdout


# ==================== client warn tests ====================

def test_client_warn_batch_and_broadcasting(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'client', 'warn',
        '-s', '600519.SH,000001.SZ',
        '-m', '突破均线',
        '-p', '1800.5',
        '--bs-flag', 'buy',
        '--json'
    ])
    assert res.exit_code == 0, res.output
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'send_warn'
    assert kwargs['count'] == 2
    assert kwargs['stock_list'] == ['600519.SH', '000001.SZ']
    assert kwargs['reason_list'] == ['突破均线', '突破均线']
    assert kwargs['price_list'] == ['1800.5', '1800.5']
    assert kwargs['bs_flag_list'] == ['0', '0']

    # Text mode
    res_text = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH', '-m', '单只预警', '-p', '10.0'
    ])
    assert res_text.exit_code == 0
    assert '预警已发送至客户端' in res_text.stdout


def test_client_warn_validation_pre_connection(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()

    # Invalid price
    res = runner.invoke(cli.cli, ['client', 'warn', '-s', '600519.SH', '-m', '测试', '-p', 'invalid_price', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # Invalid timestamp
    res = runner.invoke(cli.cli, ['client', 'warn', '-s', '600519.SH', '-m', '测试', '--time', '20261399', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()


# ==================== client file tests ====================

def test_client_file_missing_fails_pre_connection(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['client', 'file', '-f', 'nonexistent_test_file.txt', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()


def test_client_file_success(tmp_path, fake_session):
    test_f = tmp_path / 'payload.dat'
    test_f.write_text('dummy content', encoding='utf-8')

    runner = CliRunner()
    res_json = runner.invoke(cli.cli, ['client', 'file', '-f', str(test_f), '--json'])
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert data['success'] is True
    assert data['action'] == 'file'

    res_text = runner.invoke(cli.cli, ['client', 'file', '-f', str(test_f)])
    assert res_text.exit_code == 0
    assert '文件已推送至客户端' in res_text.stdout


# ==================== client bt tests ====================

def test_client_bt_json_and_semicolon(fake_session):
    runner = CliRunner()

    # JSON 2D series
    res_json = runner.invoke(cli.cli, [
        'client', 'bt', '-s', '600519.SH', '--times', '20261019,20261020',
        '--data', '[[1.0, 2.0], [1.1, 2.1]]', '--json'
    ])
    assert res_json.exit_code == 0, res_json.output
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'send_bt_data'
    assert kwargs['count'] == 2

    # Semicolon separated
    res_semi = runner.invoke(cli.cli, [
        'client', 'bt', '-s', '600519.SH', '--times', '20261019,20261020',
        '--data', '1.0,2.0;1.1,2.1'
    ])
    assert res_semi.exit_code == 0, res_semi.output
    assert '回测数据已推送至客户端' in res_semi.stdout


def test_client_bt_validation(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    runner = CliRunner()

    # Empty times
    res = runner.invoke(cli.cli, ['client', 'bt', '-s', '600519.SH', '--times', '  ', '--data', '1.0', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()

    # Invalid series numbers
    res = runner.invoke(cli.cli, ['client', 'bt', '-s', '600519.SH', '--times', '20261020', '--data', 'not_a_num', '--json'])
    assert res.exit_code == 2
    init_mock.assert_not_called()


# ==================== client panel tests ====================

def test_client_panel_csv_and_json_inputs(tmp_path, fake_session):
    csv_file = tmp_path / 'table1.csv'
    csv_file.write_text("date,val1\n20261020,10.5\n", encoding='utf-8')

    json_file = tmp_path / 'table2.json'
    json_file.write_text('[{"date": "20261020", "val2": 20.5}]', encoding='utf-8')

    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'client', 'panel',
        '-i', str(csv_file),
        '-i', str(json_file),
        '-n', 'TEST_PANEL',
        '--layout', 'vertical',
        '--titles', '表一,表二',
        '--heights', '50,50',
        '--json'
    ])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['success'] is True
    assert data['action'] == 'panel'
    assert data['sp_name'] == 'TEST_PANEL'

    # Text mode
    res_text = runner.invoke(cli.cli, [
        'client', 'panel',
        '-i', str(csv_file),
        '-n', 'TEST_PANEL_2',
    ])
    assert res_text.exit_code == 0
    assert '版面 TEST_PANEL_2 已成功发布至通达信客户端' in res_text.stdout


def test_client_panel_stdin_input(fake_session):
    runner = CliRunner()
    stdin_content = '[{"date": "20261020", "val1": 100}]'
    res = runner.invoke(cli.cli, [
        'client', 'panel', '-i', '-', '-n', 'STDIN_PANEL', '--json'
    ], input=stdin_content)
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['sp_name'] == 'STDIN_PANEL'


# ==================== client download tests ====================

def test_client_download_mutual_exclusion(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '1', '--parse', '--parse-only', '--json'
    ])
    assert res.exit_code == 2
    err = json.loads(res.stdout)
    assert "--parse 与 --parse-only 互斥" in err['error']


def test_client_download_parse_only(fake_session, monkeypatch):
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    fake_parsed = {
        'file': 'gp688318_2026.json',
        'stock': '688318.SH',
        'down_type': 1,
        'reports': {'20260930': {'top10_shareholders': [{'name': '社保基金'}]}}
    }
    monkeypatch.setattr(cli, 'parse_downloaded_file', lambda path, stk, dt, ddate: fake_parsed)

    runner = CliRunner()
    res_json = runner.invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '1', '--parse-only', '--json'
    ])
    assert res_json.exit_code == 0, res_json.output
    init_mock.assert_not_called()
    data = json.loads(res_json.stdout)
    assert data['action'] == 'parse_download'
    assert 'reports' in data['parsed']

    res_text = runner.invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '1', '--parse-only'
    ])
    assert res_text.exit_code == 0
    assert '已成功解析本地文件' in res_text.stdout


def test_client_download_with_parse(fake_session, monkeypatch):
    fake_parsed = {'file': 'dummy.json', 'stock': '688318.SH', 'down_type': 1}
    monkeypatch.setattr(cli, 'parse_downloaded_file', lambda path, stk, dt, ddate: fake_parsed)

    runner = CliRunner()
    res_json = runner.invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '1', '--parse', '--json'
    ])
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert data['action'] == 'download'
    assert 'parsed' in data

    res_text = runner.invoke(cli.cli, [
        'client', 'download', '-s', '688318.SH', '--type', '1', '--parse'
    ])
    assert res_text.exit_code == 0
    assert '下载指令已成功发送' in res_text.stdout
    assert '解析结果' in res_text.stdout


# ==================== trade order tests ====================

def test_trade_order_account_validation():
    runner = CliRunner()

    # both -a and --account
    res = runner.invoke(cli.cli, [
        'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10',
        '-a', '1', '--account', 'ACC123', '--json'
    ])
    assert res.exit_code == 2
    err = json.loads(res.stdout)
    assert "--account-id 与 --account 互斥" in err['error']

    # whitespace account
    res = runner.invoke(cli.cli, [
        'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10',
        '--account', '   ', '--json'
    ])
    assert res.exit_code == 2
    err = json.loads(res.stdout)
    assert "交易账户账号不能为空" in err['error']


def test_trade_order_preview_with_account():
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10',
        '--account', 'ACC888', '--json'
    ])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['status'] == 'preview'
    assert data['order']['account_id'] == 'ACC888'


def test_trade_order_confirm_with_account(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10',
        '--account', 'ACC888', '--strategy-name', 'my-strat', '--remark', 'my-remark',
        '--confirm', '--json'
    ])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data['success'] is True
    assert data['status'] == 'accepted'
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'order_stock'
    assert kwargs['stock_code'] == '600519.SH'


def test_parse_stock_list_stdin_invalid():
    runner = CliRunner()
    # non-string array passed via stdin to market get
    res = runner.invoke(cli.cli, ['market', 'get', '-s', '-', '--json'], input='[123, 456]')
    assert res.exit_code == 2
    assert "管道 JSON 输入必须为证券代码字符串数组" in json.loads(res.stdout)['error']

    # non-list object passed via stdin
    res2 = runner.invoke(cli.cli, ['market', 'get', '-s', '-', '--json'], input='{"key": "val"}')
    assert res2.exit_code == 2
    assert "管道 JSON 输入必须为证券代码字符串数组" in json.loads(res2.stdout)['error']

    # empty whitespace input
    res3 = runner.invoke(cli.cli, ['market', 'get', '-s', '-', '--json'], input='   \n\t  ')
    assert res3.exit_code == 2
    assert "未指定有效的股票代码" in json.loads(res3.stdout)['error']


def test_market_get_invalid_output_extension(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '-o', 'out.txt', '--json'])
    assert res.exit_code == 2
    assert "输出文件扩展名必须为 .csv、.json 或 .xlsx" in json.loads(res.stdout)['error']


def test_client_warn_broadcasting_and_length_mismatch(fake_session):
    runner = CliRunner()
    # Broadcating all scalar fields to N=2 stocks
    res = runner.invoke(cli.cli, [
        'client', 'warn',
        '-s', '600519.SH,000001.SZ',
        '-m', '单条原因',
        '-p', '1800.0',
        '--close', '1790.0',
        '--volume', '1000',
        '--bs-flag', 'sell',
        '--warn-type', '2',
        '--time', '20261020150000',
        '--json'
    ])
    assert res.exit_code == 0, res.output
    call_name, _, kwargs = fake_session.raw.calls[-1]
    assert call_name == 'send_warn'
    assert kwargs['count'] == 2
    assert kwargs['close_list'] == ['1790.0', '1790.0']
    assert kwargs['volum_list'] == ['1000', '1000']
    assert kwargs['bs_flag_list'] == ['1', '1']
    assert kwargs['warn_type_list'] == ['2', '2']
    assert kwargs['time_list'] == ['20261020150000', '20261020150000']

    # Closes length mismatch: 2 values for 3 stocks
    res_err1 = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH,000001.SZ,600000.SH',
        '-m', '测试', '-p', '10', '--close', '9,10', '--json'
    ])
    assert res_err1.exit_code == 2

    # Volumes length mismatch: 2 values for 3 stocks
    res_err2 = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH,000001.SZ,600000.SH',
        '-m', '测试', '-p', '10', '--volume', '100,200', '--json'
    ])
    assert res_err2.exit_code == 2

    # Times length mismatch: 2 values for 3 stocks
    res_err3 = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH,000001.SZ,600000.SH',
        '-m', '测试', '-p', '10', '--time', '20261020,20261019', '--json'
    ])
    assert res_err3.exit_code == 2

    # Reasons length mismatch: 2 values for 3 stocks
    res_err4 = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH,000001.SZ,600000.SH',
        '-m', 'r1|r2', '-p', '10', '--json'
    ])
    assert res_err4.exit_code == 2

    # Prices length mismatch: 2 values for 3 stocks
    res_err5 = runner.invoke(cli.cli, [
        'client', 'warn', '-s', '600519.SH,000001.SZ,600000.SH',
        '-m', 'r1', '-p', '10,20', '--json'
    ])
    assert res_err5.exit_code == 2


def test_client_panel_invalid_inputs(tmp_path, fake_session):
    runner = CliRunner()
    bad_ext = tmp_path / 'bad.doc'
    bad_ext.write_text('bad', encoding='utf-8')
    res = runner.invoke(cli.cli, ['client', 'panel', '-i', str(bad_ext), '-n', 'PANEL', '--json'])
    assert res.exit_code == 2
    assert "不支持的输入文件格式" in json.loads(res.stdout)['error']

    bad_json = tmp_path / 'bad.json'
    bad_json.write_text('12345', encoding='utf-8')
    res2 = runner.invoke(cli.cli, ['client', 'panel', '-i', str(bad_json), '-n', 'PANEL', '--json'])
    assert res2.exit_code == 2
    assert "无效的 JSON 表格格式" in json.loads(res2.stdout)['error']


def test_client_download_plain_text(fake_session):
    runner = CliRunner()
    res = runner.invoke(cli.cli, ['client', 'download', '-s', '688318.SH', '--type', '1'])
    assert res.exit_code == 0
    assert "下载指令已成功发送" in res.stdout


def test_trade_order_negative_account():
    runner = CliRunner()
    res = runner.invoke(cli.cli, [
        'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10',
        '-a', '-5', '--json'
    ])
    assert res.exit_code == 2
    assert "账户 ID 必须为非负整数" in json.loads(res.stdout)['error']


def test_trade_cancel_empty_order_and_text(fake_session):
    runner = CliRunner()
    res_err = runner.invoke(cli.cli, ['trade', 'cancel', '-o', '   ', '--json'])
    assert res_err.exit_code == 2
    assert "订单号不得为空" in json.loads(res_err.stdout)['error']

    res_text = runner.invoke(cli.cli, ['trade', 'cancel', '-o', 'fake-order-id'])
    assert res_text.exit_code == 0
    assert "SDK 已受理撤单" in res_text.stdout

