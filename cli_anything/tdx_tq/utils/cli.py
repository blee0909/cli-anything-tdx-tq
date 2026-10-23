"""One error boundary for runtime and Click parsing failures."""
import logging
import sys

import click

from ..core.errors import TdxError
from .output import to_json


def error_payload(error: Exception) -> dict:
    parameter = isinstance(error, (click.UsageError, ValueError))
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
    ctx.exit(2 if isinstance(error, (ValueError, click.UsageError)) else 1)


class JsonGroup(click.Group):
    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        args = list(sys.argv[1:] if args is None else args)
        code = 0
        try:
            result = super().main(args=args, prog_name=prog_name, complete_var=complete_var,
                                  standalone_mode=False, **extra)
            code = result if isinstance(result, int) else 0
        except click.ClickException as exc:
            emit_error(exc, '--json' in args)
            code = exc.exit_code
        except (TdxError, OSError) as exc:
            emit_error(exc, '--json' in args)
            code = 1
        except click.Abort:
            emit_error(RuntimeError('操作已中止'), '--json' in args)
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
