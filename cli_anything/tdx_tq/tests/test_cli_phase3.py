"""Comprehensive tests for Phase 3: Daemon, Timeout, Batching, and Export."""
import contextlib
import io
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.core.config import config
from cli_anything.tdx_tq.core.daemon import (
    IN_DAEMON_WORKER,
    MAX_FRAME_SIZE,
    TdxDaemonServer,
    classify_cli_action,
    daemon_state_file,
    get_daemon_status,
    recv_frame,
    remove_daemon_state,
    rpc_call,
    run_subprocess_with_deadline,
    send_frame,
    serve_daemon,
    start_daemon_background,
    stop_daemon,
    strip_global_flags,
    timeout_error_for_kind,
    try_forward_to_daemon,
    write_daemon_state,
)
from cli_anything.tdx_tq.core.errors import (
    TdxConfigError,
    TdxDataError,
    TdxExecutionUnknownError,
    TdxIncompleteDataError,
    TdxInitError,
    TdxOperationUnknownError,
    TdxParamError,
    TdxReadTimeoutError,
)
from cli_anything.tdx_tq.tests.test_contracts import Session
from cli_anything.tdx_tq.utils.cli import _parse_tdx_path_arg, _parse_timeout_arg


# ==================== Fixtures ====================

@pytest.fixture
def fake_session(monkeypatch):
    sess = Session()
    monkeypatch.setattr(cli, 'session', sess)
    return sess


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / 'config.json'
    fake_tdx = tmp_path / 'fake_tdx'
    fake_tdx.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps({'tdx_path': str(fake_tdx)}), encoding='utf-8')
    monkeypatch.setattr(config, '_config_file', cfg_file)
    monkeypatch.setattr(config, '_override_path', None)
    monkeypatch.setattr(config, '_tdx_path', str(fake_tdx))
    return tmp_path


# ==================== 1. Daemon Forwarding End-to-End ====================

def test_daemon_forwarding_end_to_end(isolated_config, fake_session):
    """Start TdxDaemonServer, write daemon.json, invoke via JsonGroup.main, verify warm session and requests."""
    server = TdxDaemonServer(config, port=0, idle_timeout=600.0)
    server.start()
    try:
        write_daemon_state({
            'pid': os.getpid(),
            'host': server.host,
            'port': server.port,
            'token': server.token,
            'tdx_path': server.tdx_path,
            'started_at': server.started_at,
            'idle_timeout': server.idle_timeout,
        }, cfg=config)

        # 1. First invocation: forwarded to daemon
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            code = cli.cli.main(['market', 'snapshot', '-s', '600519.SH', '--json'], standalone_mode=False)

        assert code == 0
        data = json.loads(out_buf.getvalue())
        assert data.get('Now') == '1.0'
        assert server.requests_served == 1
        assert fake_session.is_initialized is True

        # 2. Second invocation: session kept warm!
        out_buf2 = io.StringIO()
        with contextlib.redirect_stdout(out_buf2):
            code2 = cli.cli.main(['market', 'snapshot', '-s', '600519.SH', '--json'], standalone_mode=False)

        assert code2 == 0
        assert server.requests_served == 2
        assert fake_session.is_initialized is True

        # 3. Third invocation with --no-daemon: bypasses daemon
        out_buf3 = io.StringIO()
        with contextlib.redirect_stdout(out_buf3):
            code3 = cli.cli.main(['--no-daemon', 'market', 'snapshot', '-s', '600519.SH', '--json'], standalone_mode=False)

        assert code3 == 0
        assert server.requests_served == 2  # Daemon was not contacted
    finally:
        server.shutdown()
        remove_daemon_state(cfg=config)


def test_daemon_forwarding_stdin_pipe(isolated_config, fake_session, monkeypatch):
    """Test '-' stock input forwarded through daemon."""
    server = TdxDaemonServer(config, port=0, idle_timeout=600.0)
    server.start()
    try:
        write_daemon_state({
            'pid': os.getpid(),
            'host': server.host,
            'port': server.port,
            'token': server.token,
            'tdx_path': server.tdx_path,
            'started_at': server.started_at,
            'idle_timeout': server.idle_timeout,
        }, cfg=config)

        monkeypatch.setattr(sys, 'stdin', io.StringIO('600519.SH'))
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            code = cli.cli.main(['market', 'snapshot', '-s', '-', '--json'], standalone_mode=False)

        assert code == 0
        data = json.loads(out_buf.getvalue())
        assert data.get('Now') == '1.0'
        assert server.requests_served == 1
    finally:
        server.shutdown()
        remove_daemon_state(cfg=config)


def test_daemon_forwarding_fallback_when_daemon_unreachable(isolated_config, fake_session, monkeypatch):
    """When daemon.json points to closed port, try_forward_to_daemon cleans stale state and falls back seamlessly."""
    # Write daemon state pointing to non-existent port
    write_daemon_state({
        'pid': 999999,
        'host': '127.0.0.1',
        'port': 1,  # Port 1 is not running our daemon
        'token': 'fake_token',
        'tdx_path': str(Path(config.tdx_path).resolve()),
        'started_at': time.monotonic(),
        'idle_timeout': 1800.0,
    }, cfg=config)

    monkeypatch.setattr(sys, 'stdin', io.StringIO('600519.SH'))
    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        code = cli.cli.main(['market', 'snapshot', '-s', '-', '--json'], standalone_mode=False)

    assert code == 0
    data = json.loads(out_buf.getvalue())
    assert data.get('Now') == '1.0'
    # Stale file should have been cleaned
    assert not daemon_state_file(config).exists()


# ==================== 2. Daemon CLI Group ====================

def test_daemon_status_command(isolated_config):
    runner = CliRunner()
    # 1. Not running, JSON
    r = runner.invoke(cli.cli, ['daemon', 'status', '--json'])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data['running'] is False

    # 2. Not running, text
    r = runner.invoke(cli.cli, ['daemon', 'status'])
    assert r.exit_code == 0
    assert 'running' in r.stdout.lower()

    # 3. Running
    server = TdxDaemonServer(config, port=0)
    server.start()
    try:
        write_daemon_state({
            'pid': os.getpid(),
            'host': server.host,
            'port': server.port,
            'token': server.token,
            'tdx_path': server.tdx_path,
        }, cfg=config)

        r = runner.invoke(cli.cli, ['daemon', 'status', '--json'])
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data['running'] is True
        assert data['port'] == server.port
    finally:
        server.shutdown()
        remove_daemon_state(cfg=config)


def test_daemon_stop_command(isolated_config):
    runner = CliRunner()
    # 1. Not running, JSON
    r = runner.invoke(cli.cli, ['daemon', 'stop', '--json'])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data['success'] is True
    assert data['was_running'] is False

    # 2. Not running, text
    r = runner.invoke(cli.cli, ['daemon', 'stop'])
    assert r.exit_code == 0
    assert "守护进程未在运行" in r.stdout

    # 3. Running, JSON
    server = TdxDaemonServer(config, port=0)
    server.start()
    write_daemon_state({
        'pid': os.getpid(),
        'host': server.host,
        'port': server.port,
        'token': server.token,
        'tdx_path': server.tdx_path,
    }, cfg=config)

    r = runner.invoke(cli.cli, ['daemon', 'stop', '--json'])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data['success'] is True
    assert data['stopped'] is True
    assert data['was_running'] is True


def test_daemon_start_command(isolated_config, monkeypatch):
    runner = CliRunner()

    # 1. Validation errors
    r1 = runner.invoke(cli.cli, ['daemon', 'start', '--idle-timeout', '0', '--json'])
    assert r1.exit_code == 2
    assert json.loads(r1.stdout)['error_code'] == 'INVALID_PARAMETER'

    r2 = runner.invoke(cli.cli, ['daemon', 'start', '--idle-timeout', '-1', '--json'])
    assert r2.exit_code == 2

    r3 = runner.invoke(cli.cli, ['daemon', 'start', '--port', '-1', '--json'])
    assert r3.exit_code == 2

    r4 = runner.invoke(cli.cli, ['daemon', 'start', '--port', '70000', '--json'])
    assert r4.exit_code == 2

    # 2. Mocked start_daemon_background
    mock_start = Mock(return_value={'running': True, 'pid': 1234, 'port': 5678})
    monkeypatch.setattr('cli_anything.tdx_tq.tdx_tq_cli.start_daemon_background', mock_start)

    r_json = runner.invoke(cli.cli, ['daemon', 'start', '--port', '5678', '--idle-timeout', '120', '--json'])
    assert r_json.exit_code == 0
    data = json.loads(r_json.stdout)
    assert data['success'] is True
    assert data['pid'] == 1234
    mock_start.assert_called_with(config, idle_timeout=120.0, port=5678)

    r_text = runner.invoke(cli.cli, ['daemon', 'start'])
    assert r_text.exit_code == 0
    assert "守护进程已启动" in r_text.stdout


def test_daemon_serve_command(isolated_config, monkeypatch):
    runner = CliRunner()

    # 1. Validation errors
    r1 = runner.invoke(cli.cli, ['daemon', 'serve', '--idle-timeout', '-10'])
    assert r1.exit_code == 2

    r2 = runner.invoke(cli.cli, ['daemon', 'serve', '--port', '99999'])
    assert r2.exit_code == 2

    # 2. Mocked serve_daemon
    mock_serve = Mock()
    monkeypatch.setattr('cli_anything.tdx_tq.tdx_tq_cli.serve_daemon', mock_serve)

    r = runner.invoke(cli.cli, ['daemon', 'serve', '--port', '4321', '--idle-timeout', '300'])
    assert r.exit_code == 0
    mock_serve.assert_called_once_with(config, port=4321, idle_timeout=300.0)


def test_status_command_includes_daemon(isolated_config, fake_session):
    runner = CliRunner()
    r = runner.invoke(cli.cli, ['status', '--json'])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert 'daemon' in data
    assert data['daemon']['running'] is False


# ==================== 3. Global --timeout & Deadline Preemption ====================

def test_timeout_arg_parser_validation():
    # Valid cases
    present, val = _parse_timeout_arg(['--timeout', '5.5'])
    assert present is True and val == 5.5

    present, val = _parse_timeout_arg(['--timeout=10'])
    assert present is True and val == 10.0

    present, val = _parse_timeout_arg(['market', 'get'])
    assert present is False and val is None

    # Invalid cases raise TdxParamError
    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout', '0'])

    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout', '-1'])

    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout', 'nan'])

    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout', 'inf'])

    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout', 'abc'])

    with pytest.raises(TdxParamError):
        _parse_timeout_arg(['--timeout'])


def test_timeout_global_option_invalid_cli_rejection():
    # Test through JsonGroup.main with standalone_mode=False
    code1 = cli.cli.main(['--timeout', '0', 'market', 'get', '--json'], standalone_mode=False)
    assert code1 == 2

    code2 = cli.cli.main(['--timeout', '-5', 'status', '--json'], standalone_mode=False)
    assert code2 == 2

    code3 = cli.cli.main(['--timeout=invalid', 'status', '--json'], standalone_mode=False)
    assert code3 == 2


def test_preemptible_deadline_timeout_interception_read(monkeypatch):
    """Simulate slow read timing out in JsonGroup.main."""
    from cli_anything.tdx_tq.core.session import session as default_session

    # Ensure cli.session is default_session so is_real_session evaluates True
    monkeypatch.setattr(cli, 'session', default_session)

    mock_run = Mock(side_effect=TdxReadTimeoutError("读取请求超时", details={'action_kind': 'read'}))
    monkeypatch.setattr('cli_anything.tdx_tq.utils.cli.run_subprocess_with_deadline', mock_run)

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        code = cli.cli.main(['--timeout', '1.0', 'market', 'get', '-s', '600519.SH', '--json'], standalone_mode=False)

    assert code == 1
    payload = json.loads(out_buf.getvalue())
    assert payload['success'] is False
    assert payload['error_code'] == 'SDK_TIMEOUT'
    assert payload['retryable'] is True


def test_preemptible_deadline_timeout_interception_trade(monkeypatch):
    """Simulate slow trade timing out in JsonGroup.main."""
    from cli_anything.tdx_tq.core.session import session as default_session

    monkeypatch.setattr(cli, 'session', default_session)

    mock_run = Mock(side_effect=TdxExecutionUnknownError("交易请求超时，状态未知", details={'action_kind': 'trade_mutation'}))
    monkeypatch.setattr('cli_anything.tdx_tq.utils.cli.run_subprocess_with_deadline', mock_run)

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        code = cli.cli.main(['--timeout', '1.0', 'trade', 'order', '-s', '600519.SH', '-t', 'buy', '-v', '100', '-p', '10', '--confirm', '--json'], standalone_mode=False)

    assert code == 1
    payload = json.loads(out_buf.getvalue())
    assert payload['success'] is False
    assert payload['error_code'] == 'TRADE_STATE_UNKNOWN'
    assert payload['retryable'] is False


def test_preemptible_deadline_timeout_interception_success(monkeypatch):
    """Simulate deadline execution returning normal output."""
    from cli_anything.tdx_tq.core.session import session as default_session

    monkeypatch.setattr(cli, 'session', default_session)

    mock_run = Mock(return_value=(0, '{"ok": true}\n', ''))
    monkeypatch.setattr('cli_anything.tdx_tq.utils.cli.run_subprocess_with_deadline', mock_run)

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        code = cli.cli.main(['--timeout', '5.0', 'market', 'get', '-s', '600519.SH', '--json'], standalone_mode=False)

    assert code == 0
    assert json.loads(out_buf.getvalue()) == {'ok': True}


def test_run_subprocess_with_deadline_direct(monkeypatch):
    """Direct tests for run_subprocess_with_deadline logic."""
    with pytest.raises(TdxParamError):
        run_subprocess_with_deadline(['status'], timeout_sec=0)

    # Simulate TimeoutExpired on read
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='fake', timeout=1.0)

    monkeypatch.setattr(subprocess, 'run', raise_timeout)

    with pytest.raises(TdxReadTimeoutError) as exc_info:
        run_subprocess_with_deadline(['market', 'get', '-s', '600519.SH'], timeout_sec=1.0)
    assert exc_info.value.code == 'SDK_TIMEOUT'
    assert exc_info.value.retryable is True

    # Simulate TimeoutExpired on trade mutation
    with pytest.raises(TdxExecutionUnknownError) as exc_trade:
        run_subprocess_with_deadline(['trade', 'order', '-s', '600519.SH', '--confirm'], timeout_sec=1.0)
    assert exc_trade.value.code == 'TRADE_STATE_UNKNOWN'
    assert exc_trade.value.retryable is False

    # Simulate TimeoutExpired on op mutation
    with pytest.raises(TdxOperationUnknownError) as exc_op:
        run_subprocess_with_deadline(['sector', 'create', '-c', 'BK1', '-n', '板块'], timeout_sec=1.0)
    assert exc_op.value.code == 'OPERATION_STATE_UNKNOWN'
    assert exc_op.value.retryable is False


# ==================== 4. Market Get: Chunking, Progress & Exports ====================

def test_market_get_chunk_size_progress(fake_session, monkeypatch):
    """Verify --chunk-size emits progress to stderr and clean JSON to stdout."""
    runner = CliRunner()
    stocks = '600519.SH,000001.SZ,600000.SH,000002.SZ'

    def mock_read_with_metadata(method, **kwargs):
        req_stocks = kwargs.get('stock_list', [])
        data = {
            'close': pd.DataFrame({s: [1.0] for s in req_stocks}, index=['20261020']),
            'open': pd.DataFrame({s: [1.0] for s in req_stocks}, index=['20261020']),
            'volume': pd.DataFrame({s: [100.0] for s in req_stocks}, index=['20261020']),
        }
        return data, {}

    monkeypatch.setattr(fake_session.tdx, 'read_with_metadata', mock_read_with_metadata)

    res = runner.invoke(cli.cli, ['market', 'get', '-s', stocks, '--chunk-size', '2', '--json'])
    assert res.exit_code == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    # stdout should be clean JSON
    data = json.loads(res.stdout)
    assert isinstance(data, dict)
    assert len(data) == 4

    # stderr should contain chunk progress
    assert "[分块进度 1/2]" in res.stderr
    assert "[分块进度 2/2]" in res.stderr


def test_market_get_exports_jsonl_and_parquet(fake_session, tmp_path, monkeypatch):
    runner = CliRunner()

    # 1. Export JSONL
    out_jsonl = tmp_path / 'kline.jsonl'
    res_jsonl = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '-c', '2', '-o', str(out_jsonl), '--json'])
    assert res_jsonl.exit_code == 0
    assert out_jsonl.exists()
    lines = [json.loads(line) for line in out_jsonl.read_text(encoding='utf-8').strip().split('\n')]
    assert len(lines) > 0
    assert lines[0]['stock'] == '600519.SH'

    # 2. Export Parquet (mock DataFrame.to_parquet to test export path)
    out_parquet = tmp_path / 'kline.parquet'
    with patch.object(pd.DataFrame, 'to_parquet', return_value=None) as mock_tp:
        res_parquet = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '-c', '2', '-o', str(out_parquet), '--json'])
        assert res_parquet.exit_code == 0
        mock_tp.assert_called()

    # 3. Export Invalid Extension fails with exit 2 before connection
    init_mock = Mock(wraps=fake_session.ensure_initialized)
    monkeypatch.setattr(fake_session, 'ensure_initialized', init_mock)
    res_bad = runner.invoke(cli.cli, ['market', 'get', '-s', '600519.SH', '-o', 'bad.extension', '--json'])
    assert res_bad.exit_code == 2
    assert json.loads(res_bad.stdout)['error_code'] == 'INVALID_PARAMETER'
    init_mock.assert_not_called()


def test_market_get_phase_timings_logged(fake_session, caplog):
    runner = CliRunner()
    with caplog.at_level(logging.DEBUG, logger='cli_anything.tdx_tq'):
        res = runner.invoke(cli.cli, ['--debug', 'market', 'get', '-s', '600519.SH', '-c', '2', '--json'])
    assert res.exit_code == 0
    assert any("market get 阶段耗时:" in record.message for record in caplog.records)


# ==================== 5. Market Snapshot: Allow-Partial & Export ====================

def test_market_snapshot_allow_partial_envelope(fake_session, monkeypatch):
    runner = CliRunner()

    def mock_get_market_snapshot(stock_code, **kwargs):
        if stock_code == '000001.SZ':
            raise TdxDataError("数据不可用")
        return {'ErrorId': '0', 'Now': '1800.0'}

    monkeypatch.setattr(fake_session.tdx, 'get_market_snapshot', mock_get_market_snapshot)

    # 1. With --allow-partial: returns partial envelope
    res = runner.invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH,000001.SZ', '--allow-partial', '--json'])
    assert res.exit_code == 0
    envelope = json.loads(res.stdout)
    assert envelope['schema_version'] == 1
    assert envelope['success'] is True
    assert envelope['partial'] is True
    assert '600519.SH' in envelope['available_stocks']
    assert '000001.SZ' in envelope['missing_stocks']

    # 2. Without --allow-partial: raises and fails
    res_fail = runner.invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH,000001.SZ', '--json'])
    assert res_fail.exit_code != 0

    # 3. All stocks fail with --allow-partial: raises TdxIncompleteDataError
    def mock_all_fail(stock_code, **kwargs):
        raise TdxDataError("完全不可用")

    monkeypatch.setattr(fake_session.tdx, 'get_market_snapshot', mock_all_fail)
    res_all_fail = runner.invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH,000001.SZ', '--allow-partial', '--json'])
    assert res_all_fail.exit_code == 1
    err_envelope = json.loads(res_all_fail.stdout)
    assert err_envelope['success'] is False
    assert err_envelope['details']['partial'] is True


def test_market_snapshot_export_file(fake_session, tmp_path):
    runner = CliRunner()
    out_file = tmp_path / 'snaps.jsonl'

    # 1. Export multi-stock snapshot to JSONL
    res = runner.invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH,000001.SZ', '-o', str(out_file), '--json'])
    assert res.exit_code == 0
    assert out_file.exists()
    lines = [json.loads(line) for line in out_file.read_text(encoding='utf-8').strip().split('\n')]
    assert len(lines) == 2
    assert {l['stock'] for l in lines} == {'600519.SH', '000001.SZ'}

    # 2. Text output confirmation
    out_file_csv = tmp_path / 'snaps.csv'
    res_text = runner.invoke(cli.cli, ['market', 'snapshot', '-s', '600519.SH,000001.SZ', '-o', str(out_file_csv)])
    assert res_text.exit_code == 0
    assert "快照已成功导出至" in res_text.stdout


# ==================== 6. Core Daemon Server & RPC Unit Tests ====================

def test_strip_global_flags():
    args = [
        '--debug', '--tdx-path', 'D:\\tdx', '--timeout', '10',
        'market', 'get', '-s', '600519.SH', '--json', '--no-daemon',
        '--tdx-path=D:\\other', '--timeout=20'
    ]
    stripped = strip_global_flags(args)
    assert stripped == ['market', 'get', '-s', '600519.SH']


def test_classify_cli_action():
    assert classify_cli_action([]) == 'local_or_bypass'
    assert classify_cli_action(['--help']) == 'local_or_bypass'
    assert classify_cli_action(['status']) == 'local_or_bypass'
    assert classify_cli_action(['market', 'watch']) == 'local_or_bypass'
    assert classify_cli_action(['stock', 'list', '--categories']) == 'local_or_bypass'
    assert classify_cli_action(['stock', 'search', '--local']) == 'local_or_bypass'
    assert classify_cli_action(['client', 'download', '--parse-only']) == 'local_or_bypass'
    assert classify_cli_action(['trade', 'order', '-s', '600519.SH']) == 'local_or_bypass'
    assert classify_cli_action(['trade', 'order', '-s', '600519.SH', '--confirm']) == 'trade_mutation'
    assert classify_cli_action(['trade', 'cancel', '-o', '123']) == 'trade_mutation'
    assert classify_cli_action(['sector', 'create', '-c', 'B1']) == 'op_mutation'
    assert classify_cli_action(['client', 'warn', '-s', '600519.SH']) == 'op_mutation'
    assert classify_cli_action(['market', 'refresh']) == 'op_mutation'
    assert classify_cli_action(['market', 'get', '-s', '600519.SH']) == 'read'


def test_timeout_error_for_kind():
    err_trade = timeout_error_for_kind('trade_mutation', 2.0)
    assert isinstance(err_trade, TdxExecutionUnknownError)
    assert err_trade.code == 'TRADE_STATE_UNKNOWN'
    assert err_trade.retryable is False

    err_op = timeout_error_for_kind('op_mutation', 2.0)
    assert isinstance(err_op, TdxOperationUnknownError)
    assert err_op.code == 'OPERATION_STATE_UNKNOWN'
    assert err_op.retryable is False

    err_read = timeout_error_for_kind('read', 2.0)
    assert isinstance(err_read, TdxReadTimeoutError)
    assert err_read.code == 'SDK_TIMEOUT'
    assert err_read.retryable is True


def test_socket_framing():
    server_sock, client_sock = socket.socketpair()
    try:
        # Normal frame
        payload = {'hello': 'world', 'count': 42}
        send_frame(client_sock, payload)
        received = recv_frame(server_sock)
        assert received == payload

        # Frame exceeding MAX_FRAME_SIZE
        with pytest.raises(ValueError):
            send_frame(client_sock, {'data': 'x' * (MAX_FRAME_SIZE + 10)})

        # Header indicating oversize
        oversize_header = struct.pack('>I', MAX_FRAME_SIZE + 100)
        client_sock.sendall(oversize_header)
        with pytest.raises(ValueError):
            recv_frame(server_sock)
    finally:
        server_sock.close()
        client_sock.close()


def test_daemon_state_lifecycle(isolated_config):
    state_file = daemon_state_file(config)
    assert not state_file.is_file()

    # Write state
    data = {'pid': os.getpid(), 'token': 'abc123', 'port': 8888}
    write_daemon_state(data, cfg=config)
    assert state_file.is_file()

    # Wrong token cannot remove
    assert remove_daemon_state(token='wrong_token', cfg=config) is False
    assert state_file.is_file()

    # Right token removes
    assert remove_daemon_state(token='abc123', cfg=config) is True
    assert not state_file.is_file()


def test_daemon_server_dispatch_and_security(isolated_config):
    server = TdxDaemonServer(config, port=0, idle_timeout=1.0)
    server.start()
    try:
        # Invalid token
        resp_unauth = rpc_call(server.host, server.port, {'action': 'status', 'token': 'invalid'})
        assert resp_unauth['ok'] is False
        assert resp_unauth['error'] == 'unauthorized'

        # Ping
        resp_ping = rpc_call(server.host, server.port, {'action': 'ping', 'token': server.token})
        assert resp_ping['ok'] is True
        assert resp_ping['port'] == server.port

        # Unknown action
        resp_unknown = rpc_call(server.host, server.port, {'action': 'non_existent', 'token': server.token})
        assert resp_unknown['ok'] is False

        # Path mismatch
        resp_mismatch = rpc_call(server.host, server.port, {
            'action': 'invoke',
            'token': server.token,
            'tdx_path': 'C:\\Different\\Path',
            'args': ['status']
        })
        assert resp_mismatch['ok'] is False
        assert resp_mismatch['reason'] == 'path_mismatch'
    finally:
        server.shutdown()


def test_start_daemon_background_already_running(isolated_config, monkeypatch):
    mock_status = {'running': True, 'tdx_path': str(Path(config.tdx_path).resolve())}
    monkeypatch.setattr('cli_anything.tdx_tq.core.daemon.get_daemon_status', lambda cfg=None, timeout=0.5: mock_status)
    res = start_daemon_background(config)
    assert res['already_running'] is True

    # Path mismatch raises TdxConfigError
    mock_status_diff = {'running': True, 'tdx_path': 'D:\\Different\\Path'}
    monkeypatch.setattr('cli_anything.tdx_tq.core.daemon.get_daemon_status', lambda cfg=None, timeout=0.5: mock_status_diff)
    with pytest.raises(TdxConfigError):
        start_daemon_background(config)
