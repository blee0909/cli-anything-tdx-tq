"""One error boundary for runtime and Click parsing failures."""
import io
import logging
import math
import sys

import click

from ..core.config import config
from ..core.daemon import (
    IN_DAEMON_WORKER,
    classify_cli_action,
    daemon_state_file,
    run_subprocess_with_deadline,
    try_forward_to_daemon,
)
from ..core.errors import TdxError, TdxParamError
from .output import to_json


def error_payload(error: Exception) -> dict:
    parameter = isinstance(error, (click.UsageError, TdxParamError)) or (
        isinstance(error, ValueError) and not isinstance(error, TdxError)
    )
    payload = {'success': False, 'error': str(error), 'error_type': type(error).__name__,
               'error_code': getattr(error, 'code', 'INVALID_PARAMETER' if parameter else 'OPERATION_FAILED'),
               'retryable': bool(getattr(error, 'retryable', False))}
    if getattr(error, 'details', None) is not None:
        payload['details'] = error.details
    if hasattr(error, 'data'):
        payload['data'] = error.data
    return payload


def emit_error(error: Exception, as_json: bool, prefix: str = '操作失败') -> None:
    if as_json:
        click.echo(to_json(error_payload(error)))
    else:
        click.echo(f'{prefix}：{error}', err=True)


def handle_error(ctx, error: Exception, as_json: bool = False, prefix: str = '操作失败'):
    emit_error(error, as_json, prefix)
    is_param = isinstance(error, (click.UsageError, TdxParamError)) or (
        isinstance(error, ValueError) and not isinstance(error, TdxError)
    )
    ctx.exit(2 if is_param else 1)


def _parse_timeout_arg(args: list[str]) -> tuple[bool, float | None]:
    """Parse --timeout from args. Returns (is_present, value).
    Raises TdxParamError if present but invalid or <= 0."""
    i = 0
    n = len(args)
    found = False
    val_str = None
    while i < n:
        arg = args[i]
        if arg == '--timeout':
            found = True
            if i + 1 < n:
                val_str = args[i + 1]
                i += 2
                continue
            else:
                val_str = None
                i += 1
                continue
        elif arg.startswith('--timeout='):
            found = True
            val_str = arg.split('=', 1)[1]
            i += 1
            continue
        i += 1
    if not found:
        return False, None
    if val_str is None:
        raise TdxParamError("超时时间必须为正数")
    try:
        val = float(val_str)
    except ValueError:
        raise TdxParamError("超时时间必须为正数")
    if not math.isfinite(val) or val <= 0:
        raise TdxParamError("超时时间必须为正数")
    return True, val


def _parse_tdx_path_arg(args: list[str]) -> str | None:
    """Extract --tdx-path argument from CLI argument list if present."""
    i = 0
    n = len(args)
    while i < n:
        arg = args[i]
        if arg == '--tdx-path' and i + 1 < n:
            return args[i + 1]
        elif arg.startswith('--tdx-path='):
            return arg.split('=', 1)[1]
        i += 1
    return None


class JsonGroup(click.Group):
    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        args = list(sys.argv[1:] if args is None else args)
        as_json_output = ('--json' in args or '--jsonl' in args or '--allow-partial' in args)
        code = 0
        try:
            if not IN_DAEMON_WORKER.get():
                timeout_present, timeout_val = _parse_timeout_arg(args)
                no_daemon = ('--no-daemon' in args)
                parsed_tdx_path = _parse_tdx_path_arg(args)

                old_override = config._override_path
                if parsed_tdx_path:
                    config.override(parsed_tdx_path)
                try:
                    kind = classify_cli_action(args)
                    if kind != 'local_or_bypass':
                        if not no_daemon and daemon_state_file(config).is_file():
                            stdin_text = sys.stdin.read() if '-' in args else ''
                            forwarded = try_forward_to_daemon(
                                args, stdin_text=stdin_text, cfg=config, timeout=timeout_val
                            )
                            if forwarded is not None:
                                if forwarded.get('stdout'):
                                    click.echo(forwarded['stdout'], nl=False)
                                if forwarded.get('stderr'):
                                    click.echo(forwarded['stderr'], nl=False, err=True)
                                code = forwarded['exit_code']
                                if standalone_mode:
                                    raise SystemExit(code)
                                return code
                            elif stdin_text:
                                sys.stdin = io.StringIO(stdin_text)
                        elif timeout_val is not None:
                            import cli_anything.tdx_tq.tdx_tq_cli as cli_mod
                            from ..core.session import session as default_session
                            is_real_session = (getattr(cli_mod, 'session', None) is default_session)
                            if is_real_session:
                                stdin_text = sys.stdin.read() if '-' in args else ''
                                code, out, err = run_subprocess_with_deadline(
                                    args, stdin_text=stdin_text, timeout_sec=timeout_val
                                )
                                if out:
                                    click.echo(out, nl=False)
                                if err:
                                    click.echo(err, nl=False, err=True)
                                if standalone_mode:
                                    raise SystemExit(code)
                                return code
                finally:
                    if parsed_tdx_path:
                        config.override(old_override)

            result = super().main(args=args, prog_name=prog_name, complete_var=complete_var,
                                  standalone_mode=False, **extra)
            code = result if isinstance(result, int) else 0
        except click.ClickException as exc:
            emit_error(exc, as_json_output)
            code = exc.exit_code
        except (TdxError, OSError) as exc:
            emit_error(exc, as_json_output)
            code = 2 if isinstance(exc, TdxParamError) else 1
        except click.Abort:
            emit_error(RuntimeError('操作已中止'), as_json_output)
            code = 130
        if standalone_mode:
            raise SystemExit(code)
        return code


def configure_debug(ctx, enabled: bool) -> None:
    logger = logging.getLogger('cli_anything.tdx_tq')
    previous = logger.level
    logger.setLevel(logging.DEBUG if enabled else logging.WARNING)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(handler)
    def cleanup():
        logger.removeHandler(handler)
        logger.setLevel(previous)
    ctx.call_on_close(cleanup)
