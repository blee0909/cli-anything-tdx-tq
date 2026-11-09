"""Persistent daemon worker and preemptible deadline/timeout engine for TDX TQ."""

from __future__ import annotations

import contextlib
import hmac
import io
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from .config import config
from .errors import (
    TdxConfigError,
    TdxDataError,
    TdxError,
    TdxExecutionUnknownError,
    TdxInitError,
    TdxOperationUnknownError,
    TdxParamError,
    TdxReadTimeoutError,
)
from .session import session

__all__ = [
    'IN_DAEMON_WORKER',
    'MAX_FRAME_SIZE',
    'TdxDaemonServer',
    'classify_cli_action',
    'daemon_state_file',
    'get_daemon_status',
    'recv_frame',
    'remove_daemon_state',
    'rpc_call',
    'run_subprocess_with_deadline',
    'send_frame',
    'serve_daemon',
    'start_daemon_background',
    'stop_daemon',
    'strip_global_flags',
    'timeout_error_for_kind',
    'try_forward_to_daemon',
    'write_daemon_state',
]

# 1. Context variable indicating that a CLI command is executing inside persistent daemon worker
IN_DAEMON_WORKER: ContextVar[bool] = ContextVar('IN_DAEMON_WORKER', default=False)

MAX_FRAME_SIZE = 32 * 1024 * 1024  # 32 MB length-prefixed frame ceiling


# 2. State file resolution & atomic persistence
def daemon_state_file(cfg=None) -> Path:
    """Resolve daemon.json state file colocated with config.json."""
    c = cfg or config
    return Path(c.config_file_path).parent / 'daemon.json'


def write_daemon_state(data: dict, cfg=None) -> Path:
    """Atomically persist daemon state to daemon.json."""
    state_file = daemon_state_file(cfg)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=state_file.parent,
            prefix='.daemon-',
            suffix='.tmp',
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_file)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return state_file


def remove_daemon_state(token: str | None = None, cfg=None) -> bool:
    """Safely remove daemon.json; optionally verify token match."""
    state_file = daemon_state_file(cfg)
    if not state_file.is_file():
        return False

    if token is not None:
        try:
            content = json.loads(state_file.read_text(encoding='utf-8'))
            stored_token = content.get('token', '')
            if not isinstance(stored_token, str) or not hmac.compare_digest(stored_token, token):
                return False
        except Exception:
            return False

    try:
        state_file.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# 3. Command classification & mutation-safe timeout mapping
def strip_global_flags(args: list[str]) -> list[str]:
    """Strip global flags and options with arguments from CLI arg list."""
    stripped: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        arg = args[i]
        if arg in ('--debug', '--json', '--jsonl', '--no-daemon'):
            i += 1
            continue
        if arg in ('--tdx-path', '--timeout'):
            i += 2
            continue
        if arg.startswith('--tdx-path=') or arg.startswith('--timeout='):
            i += 1
            continue
        stripped.append(arg)
        i += 1
    return stripped


def classify_cli_action(args: list[str]) -> str:
    """Classify CLI action into local_or_bypass, trade_mutation, op_mutation, or read."""
    if not args:
        return 'local_or_bypass'
    if any(arg in ('-h', '--help', '--version') for arg in args):
        return 'local_or_bypass'

    stripped = strip_global_flags(args)
    if not stripped:
        return 'local_or_bypass'

    group = stripped[0]
    subcommand = stripped[1] if len(stripped) > 1 else ''
    rest = stripped[2:] if len(stripped) > 2 else []

    if group in ('daemon', 'config', 'status', 'init', 'close'):
        return 'local_or_bypass'
    if group == 'market' and subcommand == 'watch':
        return 'local_or_bypass'
    if group == 'stock' and subcommand == 'list' and '--categories' in rest:
        return 'local_or_bypass'
    if group == 'stock' and subcommand in ('search', 'relation') and '--local' in rest:
        return 'local_or_bypass'
    if group == 'client' and subcommand == 'download' and '--parse-only' in rest:
        return 'local_or_bypass'
    if group == 'formula' and subcommand in ('list', 'info') and '--local' in rest:
        return 'local_or_bypass'
    if group == 'trade' and subcommand == 'order' and '--confirm' not in rest:
        return 'local_or_bypass'

    if group == 'trade' and subcommand == 'order' and '--confirm' in rest:
        return 'trade_mutation'
    if group == 'trade' and subcommand == 'cancel':
        return 'trade_mutation'

    if group == 'formula' and subcommand == 'eval':
        if any(
            tok in ('--push-sector', '--push-temp', '--push-panel', '--push-warn')
            or tok.startswith('--push-sector=')
            or tok.startswith('--push-panel=')
            for tok in rest
        ):
            return 'op_mutation'
        return 'read'

    if group == 'sector' and subcommand in ('create', 'delete', 'rename', 'clear', 'update-stocks'):
        return 'op_mutation'
    if group == 'client' and subcommand in ('warn', 'msg', 'file', 'bt', 'panel', 'link', 'download'):
        return 'op_mutation'
    if group == 'market' and subcommand == 'refresh':
        return 'op_mutation'

    return 'read'


def timeout_error_for_kind(kind: str, timeout_sec: float) -> TdxError:
    """Generate exact typed error depending on command classification."""
    if kind == 'trade_mutation':
        return TdxExecutionUnknownError(
            f"交易请求超过截止时间 ({timeout_sec:g}s)，委托状态未知；请先通过 trade orders 查询核对，切勿盲目重复发单",
            details={'timeout_seconds': timeout_sec, 'action_kind': kind},
        )
    if kind == 'op_mutation':
        return TdxOperationUnknownError(
            f"操作请求超过截止时间 ({timeout_sec:g}s)，执行状态未知；请核对后再操作",
            details={'timeout_seconds': timeout_sec, 'action_kind': kind},
        )
    return TdxReadTimeoutError(
        f"读取请求超过截止时间 ({timeout_sec:g}s)",
        details={'timeout_seconds': timeout_sec, 'action_kind': kind},
    )


# 4. Length-prefixed loopback framing
def send_frame(sock: socket.socket, data: dict) -> None:
    """Send length-prefixed UTF-8 JSON frame over loopback socket."""
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError(f"Payload size {len(payload)} exceeds maximum {MAX_FRAME_SIZE}")
    header = struct.pack('>I', len(payload))
    sock.sendall(header + payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from socket, raising EOFError on unexpected closure."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            if not buf:
                raise EOFError("连接已关闭")
            raise EOFError(f"连接意外中断，仅接收 {len(buf)}/{n} 字节")
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock: socket.socket) -> dict:
    """Receive length-prefixed UTF-8 JSON frame from socket."""
    header = _recv_exact(sock, 4)
    (length,) = struct.unpack('>I', header)
    if length > MAX_FRAME_SIZE:
        raise ValueError(f"Frame length {length} exceeds maximum {MAX_FRAME_SIZE}")
    if length == 0:
        return {}
    body = _recv_exact(sock, length)
    return json.loads(body.decode('utf-8'))


def rpc_call(
    host: str,
    port: int,
    request: dict,
    *,
    timeout: float | None = None,
    connect_timeout: float = 0.5,
) -> dict:
    """Perform single length-prefixed request/response RPC over loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout)
    try:
        sock.connect((host, port))
        send_frame(sock, request)
        sock.settimeout(timeout)
        response = recv_frame(sock)
        return response
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _default_cli_runner(args: list[str], stdin_text: str = '') -> tuple[int, str, str]:
    """Default in-process runner invoking Click root command."""
    from ..tdx_tq_cli import cli

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    code = 0
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code = cli.main(args=args, standalone_mode=False)
            if code is None:
                code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        code = 1
        err_buf.write(f"\n{exc}")
    finally:
        sys.stdin = old_stdin
    return (code, out_buf.getvalue(), err_buf.getvalue())


# 5. Persistent Session Daemon Server & Lifecycle
class TdxDaemonServer:
    """Loopback persistent session daemon server."""

    def __init__(
        self,
        cfg=None,
        *,
        port: int = 0,
        idle_timeout: float = 1800.0,
        token: str | None = None,
        runner_fn: Callable[[list[str], str], tuple[int, str, str]] | None = None,
    ):
        self.cfg = cfg or config
        self.exec_lock = threading.Lock()
        self.tdx_path = str(Path(self.cfg.tdx_path).resolve())
        self.token = token or secrets.token_hex(32)
        self.idle_timeout = float(idle_timeout)
        self.started_at = time.monotonic()
        self.last_activity = time.monotonic()
        self.requests_served = 0
        self.runner_fn = runner_fn or _default_cli_runner
        self.host = '127.0.0.1'
        self.port = port
        self.server_socket: socket.socket | None = None
        self.running = False
        self.shutdown_event = threading.Event()
        self.server_thread: threading.Thread | None = None
        self.idle_thread: threading.Thread | None = None

    def _bind_and_listen(self) -> None:
        if self.running:
            return
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen(128)
        self.running = True
        self.shutdown_event.clear()

        if self.idle_timeout > 0:
            self.idle_thread = threading.Thread(
                target=self._idle_monitor, daemon=True, name="TdxDaemonIdleMonitor"
            )
            self.idle_thread.start()

    def start(self) -> None:
        """Bind server socket to loopback and start serving in background thread."""
        self._bind_and_listen()
        if self.server_thread is None or not self.server_thread.is_alive():
            self.server_thread = threading.Thread(
                target=self.serve_forever, daemon=True, name="TdxDaemonServerLoop"
            )
            self.server_thread.start()

    def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        """Wait until server is signaled to shut down."""
        return self.shutdown_event.wait(timeout)

    def _idle_monitor(self) -> None:
        interval = max(0.01, min(1.0, self.idle_timeout / 4.0))
        while not self.shutdown_event.wait(interval):
            if self.idle_timeout > 0 and (time.monotonic() - self.last_activity >= self.idle_timeout):
                self.shutdown()
                break

    def shutdown(self) -> None:
        """Signal clean server shutdown and close socket."""
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        if (
            self.server_thread
            and self.server_thread.is_alive()
            and self.server_thread != threading.current_thread()
        ):
            self.server_thread.join(timeout=2.0)

    def serve_forever(self) -> None:
        """Listen and dispatch client requests until shutdown."""
        if not self.running:
            self._bind_and_listen()
        assert self.server_socket is not None
        self.server_socket.settimeout(0.2)
        while not self.shutdown_event.is_set():
            try:
                conn, _ = self.server_socket.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            client_thread = threading.Thread(
                target=self._handle_client, args=(conn,), daemon=True
            )
            client_thread.start()

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            while not self.shutdown_event.is_set():
                try:
                    req = recv_frame(conn)
                except (EOFError, socket.timeout):
                    break
                except Exception:
                    break

                resp = self._dispatch(req)
                try:
                    send_frame(conn, resp)
                except OSError:
                    break

                if req.get('action') == 'shutdown':
                    threading.Thread(target=self.shutdown, daemon=True).start()
                    break
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, req: dict) -> dict:
        client_token = req.get('token', '')
        if not isinstance(client_token, str) or not hmac.compare_digest(client_token, self.token):
            return {'ok': False, 'error': 'unauthorized', 'message': 'Invalid token'}

        action = req.get('action', '')
        if action in ('ping', 'status'):
            return {
                'ok': True,
                'pid': os.getpid(),
                'host': self.host,
                'port': self.port,
                'tdx_path': self.tdx_path,
                'session_initialized': session.is_initialized,
                'session_state': getattr(session, 'state', 'unknown'),
                'run_id': getattr(session._tdx, 'run_id', -1) if session.is_initialized else -1,
                'requests_served': self.requests_served,
                'uptime_seconds': round(time.monotonic() - self.started_at, 2),
                'idle_seconds': round(time.monotonic() - self.last_activity, 2),
                'idle_timeout': self.idle_timeout,
            }

        if action == 'shutdown':
            return {'ok': True, 'stopped': True}

        if action == 'invoke':
            req_path = req.get('tdx_path', '')
            try:
                if not req_path or Path(req_path).resolve() != Path(self.tdx_path).resolve():
                    return {'ok': False, 'reason': 'path_mismatch'}
            except OSError:
                return {'ok': False, 'reason': 'path_mismatch'}

            with self.exec_lock:
                self.last_activity = time.monotonic()
                t0 = time.monotonic()
                token_val = IN_DAEMON_WORKER.set(True)
                out_io = io.StringIO()
                err_io = io.StringIO()
                old_stdin = sys.stdin
                sys.stdin = io.StringIO(req.get('stdin', '') or '')
                code = 0
                out_str = ''
                err_str = ''
                try:
                    with contextlib.redirect_stdout(out_io), contextlib.redirect_stderr(err_io):
                        res = self.runner_fn(req.get('args', []), req.get('stdin', '') or '')
                        if isinstance(res, tuple) and len(res) == 3:
                            c, o, e = res
                            code = c
                            out_str = o if (o is not None and o != '') else out_io.getvalue()
                            err_str = e if (e is not None and e != '') else err_io.getvalue()
                        elif isinstance(res, int):
                            code = res
                            out_str = out_io.getvalue()
                            err_str = err_io.getvalue()
                        else:
                            code = 0
                            out_str = out_io.getvalue()
                            err_str = err_io.getvalue()
                except Exception as exc:
                    code = 1
                    out_str = out_io.getvalue()
                    err_str = (err_io.getvalue() + f"\n{exc}").strip()
                finally:
                    sys.stdin = old_stdin
                    IN_DAEMON_WORKER.reset(token_val)

                ms = (time.monotonic() - t0) * 1000.0
                self.requests_served += 1
                self.last_activity = time.monotonic()
                return {
                    'ok': True,
                    'exit_code': code,
                    'stdout': out_str,
                    'stderr': err_str,
                    'duration_ms': round(ms, 1),
                }

        return {'ok': False, 'error': f"Unknown action: {action}"}


def serve_daemon(
    cfg=None,
    *,
    port: int = 0,
    idle_timeout: float = 1800.0,
    runner_fn: Callable[[list[str], str], tuple[int, str, str]] | None = None,
    ready_event: threading.Event | None = None,
) -> None:
    """Foreground server entrypoint with guaranteed cleanup of session & state file."""
    c = cfg or config
    server = TdxDaemonServer(c, port=port, idle_timeout=idle_timeout, runner_fn=runner_fn)
    server.start()
    state_data = {
        'pid': os.getpid(),
        'host': server.host,
        'port': server.port,
        'token': server.token,
        'tdx_path': server.tdx_path,
        'started_at': server.started_at,
        'idle_timeout': server.idle_timeout,
    }
    write_daemon_state(state_data, cfg=c)
    if ready_event is not None:
        ready_event.set()
    try:
        server.wait_for_shutdown()
    finally:
        try:
            session.close()
        except Exception:
            pass
        remove_daemon_state(token=server.token, cfg=c)
        server.shutdown()


# 6. Client Helpers
def get_daemon_status(cfg=None, timeout: float = 1.5) -> dict:
    """Read daemon state and ping server; cleans stale file if unreachable."""
    state_file = daemon_state_file(cfg)
    if not state_file.is_file():
        return {'running': False, 'state_file': str(state_file)}

    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
        host = state.get('host', '127.0.0.1')
        port = int(state['port'])
        token = str(state['token'])
    except Exception:
        remove_daemon_state(cfg=cfg)
        return {'running': False, 'stale_cleaned': True, 'state_file': str(state_file)}

    try:
        resp = rpc_call(host, port, {'action': 'ping', 'token': token}, timeout=timeout)
        if not resp.get('ok', False):
            remove_daemon_state(token=token, cfg=cfg)
            return {'running': False, 'stale_cleaned': True, 'state_file': str(state_file)}
    except (OSError, socket.timeout, TimeoutError, EOFError, ValueError):
        remove_daemon_state(token=token, cfg=cfg)
        return {'running': False, 'stale_cleaned': True, 'state_file': str(state_file)}

    data = dict(resp)
    data.pop('ok', None)
    return {'running': True, **data, 'state_file': str(state_file)}


def _extract_timeout_from_args(args: list[str]) -> float | None:
    """Extract --timeout argument from CLI argument list if present."""
    i = 0
    n = len(args)
    while i < n:
        arg = args[i]
        if arg == '--timeout' and i + 1 < n:
            try:
                return float(args[i + 1])
            except ValueError:
                return None
        if arg.startswith('--timeout='):
            try:
                return float(arg.split('=', 1)[1])
            except ValueError:
                return None
        i += 1
    return None


def try_forward_to_daemon(
    args: list[str],
    *,
    stdin_text: str = '',
    cfg=None,
    timeout: float | None = None,
) -> dict | None:
    """Forward command to running daemon worker if eligible; seamless fallback on disconnect."""
    kind = classify_cli_action(args)
    if kind == 'local_or_bypass':
        return None

    if timeout is None:
        timeout = _extract_timeout_from_args(args)
    if timeout is not None and timeout <= 0:
        raise TdxParamError("超时时间必须大于 0 秒", details={'timeout_seconds': timeout})

    state_file = daemon_state_file(cfg)
    if not state_file.is_file():
        return None

    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
        host = state.get('host', '127.0.0.1')
        port = int(state['port'])
        token = str(state['token'])
        state_tdx_path = state.get('tdx_path', '')
    except Exception:
        remove_daemon_state(cfg=cfg)
        return None

    resolved_cfg_path = str(Path((cfg or config).tdx_path).resolve())
    try:
        if Path(state_tdx_path).resolve() != Path(resolved_cfg_path):
            return None
    except OSError:
        return None

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, port))
    except (OSError, socket.timeout):
        try:
            sock.close()
        except OSError:
            pass
        remove_daemon_state(token=token, cfg=cfg)
        return None

    dispatched = False
    try:
        req = {
            'action': 'invoke',
            'token': token,
            'tdx_path': resolved_cfg_path,
            'args': args,
            'stdin': stdin_text,
        }
        send_frame(sock, req)
        dispatched = True
        sock.settimeout(timeout)
        resp = recv_frame(sock)
    except (socket.timeout, TimeoutError) as exc:
        if dispatched:
            raise timeout_error_for_kind(kind, timeout if timeout is not None else 0.0) from exc
        return None
    except (OSError, EOFError) as exc:
        if dispatched:
            if kind == 'trade_mutation':
                raise TdxExecutionUnknownError(
                    "交易请求发送后与守护进程的连接中断，委托状态未知；请先通过 trade orders 查询核对，切勿盲目重复发单",
                    details={'action_kind': kind},
                ) from exc
            if kind == 'op_mutation':
                raise TdxOperationUnknownError(
                    "操作请求发送后与守护进程的连接中断，执行状态未知；请核对后再操作",
                    details={'action_kind': kind},
                ) from exc
            return None
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not resp.get('ok', False):
        if resp.get('reason') == 'path_mismatch':
            return None
        return None

    return {
        'exit_code': resp.get('exit_code', 0),
        'stdout': resp.get('stdout', ''),
        'stderr': resp.get('stderr', ''),
        'duration_ms': resp.get('duration_ms', 0.0),
    }


def start_daemon_background(
    cfg=None,
    *,
    idle_timeout: float = 1800.0,
    port: int = 0,
    startup_timeout: float = 10.0,
    command: list[str] | None = None,
) -> dict:
    """Spawn detached background daemon process and wait for ready state."""
    c = cfg or config
    current_status = get_daemon_status(c)
    target_tdx_path = str(Path(c.tdx_path).resolve())
    if current_status.get('running'):
        running_path = str(Path(current_status.get('tdx_path', '')).resolve())
        if running_path == target_tdx_path:
            return {**current_status, 'already_running': True}
        raise TdxConfigError(
            f"后台守护进程已在运行，但路径不同 ({running_path} != {target_tdx_path})，请先使用 daemon stop 停止旧进程"
        )

    cmd = command or [
        sys.executable,
        '-X',
        'utf8',
        '-m',
        'cli_anything.tdx_tq.tdx_tq_cli',
        '--tdx-path',
        c.tdx_path,
        'daemon',
        'serve',
        '--idle-timeout',
        str(idle_timeout),
        '--port',
        str(port),
    ]

    kwargs: dict[str, Any] = {
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
    }
    if sys.platform == 'win32':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creationflags |= subprocess.CREATE_NO_WINDOW
        kwargs['creationflags'] = creationflags
    else:
        kwargs['start_new_session'] = True

    proc = subprocess.Popen(cmd, **kwargs)

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise TdxInitError(f"启动守护进程失败，子进程过早退出 (exit_code={proc.returncode})")
        status = get_daemon_status(c, timeout=0.5)
        if status.get('running'):
            return status
        time.sleep(0.1)

    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    raise TdxInitError(f"启动守护进程超时 ({startup_timeout:g}s)")


def stop_daemon(cfg=None, timeout: float = 5.0) -> dict:
    """Stop running daemon worker via shutdown RPC."""
    st = get_daemon_status(cfg, timeout=min(1.5, timeout))
    if not st.get('running'):
        return {'stopped': False, 'was_running': False}

    state_file = daemon_state_file(cfg)
    token: str | None = None
    pid = st.get('pid')
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding='utf-8'))
            token = state.get('token')
            if pid is None:
                pid = state.get('pid')
        except Exception:
            pass

    try:
        rpc_call(st['host'], st['port'], {'action': 'shutdown', 'token': token or ''}, timeout=timeout)
    except Exception:
        pass

    deadline = time.monotonic() + min(timeout, 2.0)
    while time.monotonic() < deadline:
        if not daemon_state_file(cfg).exists():
            break
        time.sleep(0.05)

    remove_daemon_state(token=token, cfg=cfg)
    return {'stopped': True, 'was_running': True, 'pid': pid}


def _strip_timeout_flags(args: list[str]) -> list[str]:
    """Strip --timeout flags from child arguments."""
    result: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        arg = args[i]
        if arg == '--timeout':
            i += 2
            continue
        if arg.startswith('--timeout='):
            i += 1
            continue
        result.append(arg)
        i += 1
    return result


def run_subprocess_with_deadline(
    args: list[str],
    *,
    stdin_text: str = '',
    timeout_sec: float,
    command_prefix: list[str] | None = None,
) -> tuple[int, str, str]:
    """Execute direct CLI subprocess with preemptible deadline and typed timeout error."""
    if timeout_sec <= 0:
        raise TdxParamError("超时时间必须大于 0 秒", details={'timeout_seconds': timeout_sec})

    kind = classify_cli_action(args)
    child_args = _strip_timeout_flags(args)
    base_cmd = command_prefix or [
        sys.executable,
        '-X',
        'utf8',
        '-m',
        'cli_anything.tdx_tq.tdx_tq_cli',
    ]
    full_cmd = [*base_cmd, '--no-daemon', *child_args]

    try:
        proc = subprocess.run(
            full_cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_sec,
        )
        return (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        raise timeout_error_for_kind(kind, timeout_sec) from exc
