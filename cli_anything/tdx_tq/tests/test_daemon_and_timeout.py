"""Tests for persistent session daemon worker and preemptible deadline/timeout engine."""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

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
    TdxExecutionUnknownError,
    TdxOperationUnknownError,
    TdxParamError,
    TdxReadTimeoutError,
)
from cli_anything.tdx_tq.core.session import session


# 1. Action classification tests
def test_classify_cli_action_local_or_bypass():
    assert classify_cli_action([]) == 'local_or_bypass'
    assert classify_cli_action(['-h']) == 'local_or_bypass'
    assert classify_cli_action(['--help']) == 'local_or_bypass'
    assert classify_cli_action(['--version']) == 'local_or_bypass'
    assert classify_cli_action(['market', 'get', '-h']) == 'local_or_bypass'

    for top in ('daemon', 'config', 'status', 'init', 'close'):
        assert classify_cli_action([top]) == 'local_or_bypass'
        assert classify_cli_action([top, 'subcmd']) == 'local_or_bypass'

    assert classify_cli_action(['market', 'watch', '-s', '600000']) == 'local_or_bypass'
    assert classify_cli_action(['stock', 'list', '--categories']) == 'local_or_bypass'
    assert classify_cli_action(['stock', 'search', '--local', '平安']) == 'local_or_bypass'
    assert classify_cli_action(['stock', 'relation', '--local', '600000']) == 'local_or_bypass'
    assert classify_cli_action(['client', 'download', '--parse-only', 'file.dat']) == 'local_or_bypass'
    assert classify_cli_action(['trade', 'order', '-s', '600000', '-v', '100']) == 'local_or_bypass'


def test_classify_cli_action_mutations():
    # Trade mutations
    assert classify_cli_action(['trade', 'order', '--confirm', '-s', '600000']) == 'trade_mutation'
    assert classify_cli_action(['trade', 'order', '-s', '600000', '--confirm']) == 'trade_mutation'
    assert classify_cli_action(['trade', 'cancel', '--id', '123']) == 'trade_mutation'

    # Op mutations
    for sub in ('create', 'delete', 'rename', 'clear', 'update-stocks'):
        assert classify_cli_action(['sector', sub, 'my_sector']) == 'op_mutation'

    for sub in ('warn', 'msg', 'file', 'bt', 'panel', 'link', 'download'):
        assert classify_cli_action(['client', sub, 'payload']) == 'op_mutation'

    assert classify_cli_action(['market', 'refresh']) == 'op_mutation'


def test_classify_cli_action_read_and_flag_stripping():
    assert classify_cli_action(['market', 'get', '-s', '600000']) == 'read'
    assert classify_cli_action(['stock', 'list']) == 'read'
    assert classify_cli_action(['trade', 'orders']) == 'read'
    assert classify_cli_action(['trade', 'positions']) == 'read'
    assert classify_cli_action(['trade', 'account']) == 'read'
    assert classify_cli_action(['trade', 'asset']) == 'read'

    # Stripping flags
    assert classify_cli_action(['--debug', '--json', 'trade', 'order', '--confirm']) == 'trade_mutation'
    assert classify_cli_action(['--tdx-path', r'D:\tdx', 'market', 'get']) == 'read'
    assert classify_cli_action(['--tdx-path=D:\\tdx', '--timeout=5', 'sector', 'create', 'sec']) == 'op_mutation'
    assert strip_global_flags(['--debug', '--timeout', '10', 'market', 'get']) == ['market', 'get']


# 2. Timeout error mapping tests
def test_timeout_error_for_kind():
    trade_err = timeout_error_for_kind('trade_mutation', 5.0)
    assert isinstance(trade_err, TdxExecutionUnknownError)
    assert trade_err.retryable is False
    assert trade_err.code == "TRADE_STATE_UNKNOWN"
    assert "切勿盲目重复发单" in str(trade_err)

    op_err = timeout_error_for_kind('op_mutation', 3.5)
    assert isinstance(op_err, TdxOperationUnknownError)
    assert op_err.retryable is False
    assert op_err.code == "OPERATION_STATE_UNKNOWN"
    assert "操作请求超过截止时间" in str(op_err)

    read_err = timeout_error_for_kind('read', 2.0)
    assert isinstance(read_err, TdxReadTimeoutError)
    assert read_err.retryable is True
    assert read_err.code == "SDK_TIMEOUT"


# 3. Framing send / recv tests
def test_framing_send_recv():
    s1, s2 = socket.socketpair()
    try:
        data = {'action': 'ping', 'nested': {'count': 42, 'items': ['a', 'b']}}
        send_frame(s1, data)
        received = recv_frame(s2)
        assert received == data

        # Test oversize payload rejection
        with pytest.raises(ValueError, match="exceeds maximum"):
            send_frame(s1, {'huge': 'x' * (MAX_FRAME_SIZE + 10)})
    finally:
        s1.close()
        s2.close()


def test_framing_eof_on_closed_connection():
    s1, s2 = socket.socketpair()
    s1.close()
    try:
        with pytest.raises(EOFError):
            recv_frame(s2)
    finally:
        s2.close()


# 4. Token authentication rejection
def test_token_authentication_rejection():
    server = TdxDaemonServer(token='test_token_secret_123')
    server.start()
    try:
        # Wrong token
        resp_wrong = rpc_call(server.host, server.port, {'action': 'ping', 'token': 'wrong_token'})
        assert resp_wrong.get('ok') is False
        assert resp_wrong.get('error') == 'unauthorized'

        # Correct token
        resp_ok = rpc_call(server.host, server.port, {'action': 'ping', 'token': 'test_token_secret_123'})
        assert resp_ok.get('ok') is True
        assert resp_ok.get('host') == '127.0.0.1'
        assert resp_ok.get('port') == server.port
    finally:
        server.shutdown()


# 5. Warm session persistence and IN_DAEMON_WORKER contextvar
def test_warm_session_persistence_and_in_daemon_worker():
    assert IN_DAEMON_WORKER.get() is False

    worker_states = []
    invocations = []

    def mock_runner(args: list[str], stdin_text: str) -> tuple[int, str, str]:
        worker_states.append(IN_DAEMON_WORKER.get())
        invocations.append(args)
        return (0, f"invoked_{len(invocations)}", "")

    server = TdxDaemonServer(runner_fn=mock_runner)
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
        })

        # Outside worker, contextvar is False
        assert IN_DAEMON_WORKER.get() is False

        # First invoke via try_forward_to_daemon
        res1 = try_forward_to_daemon(['market', 'get', '-s', '600000'])
        assert res1 is not None
        assert res1['exit_code'] == 0
        assert res1['stdout'] == 'invoked_1'

        # Second invoke via try_forward_to_daemon
        res2 = try_forward_to_daemon(['market', 'get', '-s', '600001'])
        assert res2 is not None
        assert res2['exit_code'] == 0
        assert res2['stdout'] == 'invoked_2'

        # Inside runner, IN_DAEMON_WORKER was True both times
        assert worker_states == [True, True]
        assert IN_DAEMON_WORKER.get() is False

        # Verify server state tracking
        status = get_daemon_status()
        assert status['running'] is True
        assert status['requests_served'] == 2
    finally:
        server.shutdown()
        remove_daemon_state()


# 6. TDX path mismatch returns None
def test_tdx_path_mismatch_returns_none(monkeypatch, tmp_path):
    server = TdxDaemonServer()
    server.start()
    try:
        # State file recorded with a different tdx path
        other_path = tmp_path / 'non_matching_tdx'
        other_path.mkdir(exist_ok=True)
        write_daemon_state({
            'pid': os.getpid(),
            'host': server.host,
            'port': server.port,
            'token': server.token,
            'tdx_path': str(other_path.resolve()),
            'started_at': server.started_at,
            'idle_timeout': server.idle_timeout,
        })

        # Current config points to default (e.g. D:\tdx), mismatch should return None immediately
        res = try_forward_to_daemon(['market', 'get', '-s', '600000'])
        assert res is None
    finally:
        server.shutdown()
        remove_daemon_state()


# 7. Stale daemon.json auto-cleanup on dead port
def test_stale_daemon_state_auto_cleanup():
    # Reserve and close a local port so it's guaranteed closed
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    temp_sock.bind(('127.0.0.1', 0))
    dead_port = temp_sock.getsockname()[1]
    temp_sock.close()

    write_daemon_state({
        'pid': 999999,
        'host': '127.0.0.1',
        'port': dead_port,
        'token': 'dead_token_123',
        'tdx_path': str(Path(config.tdx_path).resolve()),
        'started_at': time.monotonic(),
        'idle_timeout': 1800.0,
    })
    state_file = daemon_state_file()
    assert state_file.is_file()

    status = get_daemon_status(timeout=0.2)
    assert status['running'] is False
    assert status.get('stale_cleaned') is True
    assert not state_file.exists()

    # Repeat check with try_forward_to_daemon
    write_daemon_state({
        'pid': 999999,
        'host': '127.0.0.1',
        'port': dead_port,
        'token': 'dead_token_123',
        'tdx_path': str(Path(config.tdx_path).resolve()),
        'started_at': time.monotonic(),
        'idle_timeout': 1800.0,
    })
    assert state_file.is_file()
    res = try_forward_to_daemon(['market', 'get', '-s', '600000'])
    assert res is None
    assert not state_file.exists()


# 8. Idle timeout auto-shutdown & session.close invocation
def test_idle_timeout_auto_shutdown_and_session_close(monkeypatch):
    closed = False

    def mock_close():
        nonlocal closed
        closed = True

    monkeypatch.setattr(session, 'close', mock_close)

    ready = threading.Event()
    srv_thread = threading.Thread(
        target=serve_daemon,
        kwargs={'idle_timeout': 0.2, 'ready_event': ready},
        daemon=True,
    )
    srv_thread.start()
    assert ready.wait(timeout=3.0)

    state_file = daemon_state_file()
    assert state_file.is_file()

    # Wait for idle timeout (0.2s) to trigger shutdown
    srv_thread.join(timeout=3.0)
    assert not srv_thread.is_alive()
    assert closed is True
    assert not state_file.exists()


# 9. Socket timeout on slow commands (typed errors & retryable flag)
def test_socket_timeout_on_slow_commands():
    def slow_runner(args: list[str], stdin_text: str) -> tuple[int, str, str]:
        time.sleep(0.5)
        return (0, "slow_done", "")

    server = TdxDaemonServer(runner_fn=slow_runner)
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
        })

        # 1. Slow read -> TdxReadTimeoutError (retryable = True)
        with pytest.raises(TdxReadTimeoutError) as exc_read:
            try_forward_to_daemon(['market', 'get', '-s', '600000'], timeout=0.05)
        assert exc_read.value.retryable is True
        assert exc_read.value.code == "SDK_TIMEOUT"

        # 2. Slow trade mutation -> TdxExecutionUnknownError (retryable = False)
        with pytest.raises(TdxExecutionUnknownError) as exc_trade:
            try_forward_to_daemon(['trade', 'order', '--confirm', '-s', '600000'], timeout=0.05)
        assert exc_trade.value.retryable is False
        assert exc_trade.value.code == "TRADE_STATE_UNKNOWN"

        # 3. Slow op mutation -> TdxOperationUnknownError (retryable = False)
        with pytest.raises(TdxOperationUnknownError) as exc_op:
            try_forward_to_daemon(['sector', 'create', 'test_sec'], timeout=0.05)
        assert exc_op.value.retryable is False
        assert exc_op.value.code == "OPERATION_STATE_UNKNOWN"
    finally:
        server.shutdown()
        remove_daemon_state()


# 10. Connection drop after dispatch NEVER falls back for mutations
def test_connection_drop_after_dispatch_never_falls_back():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(('127.0.0.1', 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(10)
    server_sock.settimeout(0.5)

    stop_mock = threading.Event()

    def mock_server_loop():
        while not stop_mock.is_set():
            try:
                conn, _ = server_sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                # Read incoming frame then immediately drop connection without response
                _ = recv_frame(conn)
            finally:
                conn.close()

    t = threading.Thread(target=mock_server_loop, daemon=True)
    t.start()

    token = 'test_drop_token'
    write_daemon_state({
        'pid': os.getpid(),
        'host': '127.0.0.1',
        'port': port,
        'token': token,
        'tdx_path': str(Path(config.tdx_path).resolve()),
        'started_at': time.monotonic(),
        'idle_timeout': 1800.0,
    })

    try:
        # Trade mutation must raise TdxExecutionUnknownError (never return None or retry!)
        with pytest.raises(TdxExecutionUnknownError):
            try_forward_to_daemon(['trade', 'order', '--confirm', '-s', '600000'])

        # Op mutation must raise TdxOperationUnknownError
        with pytest.raises(TdxOperationUnknownError):
            try_forward_to_daemon(['sector', 'delete', 'sec1'])

        # Read command safely returns None on connection drop
        res = try_forward_to_daemon(['market', 'get', '-s', '600000'])
        assert res is None
    finally:
        stop_mock.set()
        server_sock.close()
        t.join(timeout=1.0)
        remove_daemon_state()


# 11. Subprocess deadline execution
def test_run_subprocess_with_deadline_invalid_timeout():
    with pytest.raises(TdxParamError, match="大于 0 秒"):
        run_subprocess_with_deadline(['market', 'get'], timeout_sec=0.0)

    with pytest.raises(TdxParamError, match="大于 0 秒"):
        run_subprocess_with_deadline(['market', 'get'], timeout_sec=-1.5)


def test_run_subprocess_with_deadline_terminates_in_subsecond():
    sleeping_cmd = [sys.executable, '-c', 'import time; time.sleep(10)']

    # Read timeout -> TdxReadTimeoutError
    t0 = time.monotonic()
    with pytest.raises(TdxReadTimeoutError) as exc_read:
        run_subprocess_with_deadline(['market', 'get'], timeout_sec=0.2, command_prefix=sleeping_cmd)
    duration = time.monotonic() - t0
    assert duration < 1.0
    assert exc_read.value.retryable is True

    # Trade mutation timeout -> TdxExecutionUnknownError
    t1 = time.monotonic()
    with pytest.raises(TdxExecutionUnknownError) as exc_trade:
        run_subprocess_with_deadline(
            ['trade', 'order', '--confirm'], timeout_sec=0.2, command_prefix=sleeping_cmd
        )
    duration_trade = time.monotonic() - t1
    assert duration_trade < 1.0
    assert exc_trade.value.retryable is False

    # Op mutation timeout -> TdxOperationUnknownError
    with pytest.raises(TdxOperationUnknownError) as exc_op:
        run_subprocess_with_deadline(
            ['sector', 'rename', 'a', 'b'], timeout_sec=0.2, command_prefix=sleeping_cmd
        )
    assert exc_op.value.retryable is False


def test_run_subprocess_with_deadline_success():
    fast_cmd = [sys.executable, '-c', 'import sys; print("success_output"); sys.exit(0)']
    code, stdout, stderr = run_subprocess_with_deadline(
        ['market', 'get'], timeout_sec=5.0, command_prefix=fast_cmd
    )
    assert code == 0
    assert "success_output" in stdout


# 12. start_daemon_background & stop_daemon lifecycle
def test_daemon_lifecycle_background_and_stop(tmp_path):
    # stop_daemon when not running
    res = stop_daemon()
    assert res == {'stopped': False, 'was_running': False}

    # Start in-thread daemon server mimicking background runner
    server = TdxDaemonServer()
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
        })

        # When running with same tdx_path, start_daemon_background returns already_running
        status = start_daemon_background()
        assert status.get('running') is True
        assert status.get('already_running') is True

        # When running with different tdx_path, start_daemon_background raises TdxConfigError
        diff_cfg = type('MockCfg', (), {'tdx_path': str((tmp_path / 'diff_tdx').resolve()), 'config_file_path': str(daemon_state_file())})()
        with pytest.raises(TdxConfigError, match="后台守护进程已在运行，但路径不同"):
            start_daemon_background(cfg=diff_cfg)

        # Stop daemon
        stop_res = stop_daemon()
        assert stop_res['stopped'] is True
        assert stop_res['was_running'] is True
        assert not daemon_state_file().exists()
    finally:
        server.shutdown()
        remove_daemon_state()
