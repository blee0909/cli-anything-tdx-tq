"""Regressions for misleading examples in the linked WeChat article."""
import json
from pathlib import Path
import re
import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from cli_anything.tdx_tq import tdx_tq_cli as cli
from cli_anything.tdx_tq.tests.test_contracts import Session


@pytest.mark.parametrize("args", [
    ["market", "zdt"],
    ["sector", "create", "-n", "AI突破选股"],
    ["sector", "create", "-n", "核心资产池"],
    ["sector", "update-stocks", "-s", "AI突破选股", "-c", "600519.SH,000001.SZ,600487.SH"],
    ["sector", "update-stocks", "-s", "核心资产池", "-c", "600519.SH,601318.SH"],
    ["financial", "trading", "-s", "600519.SH"],
])
def test_article_invalid_examples_never_connect(args, monkeypatch):
    connect = Mock(side_effect=AssertionError("Invalid examples must not connect"))
    monkeypatch.setattr(cli.session, "ensure_initialized", connect)
    result = CliRunner().invoke(cli.cli, args + ["--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error_code"] == "INVALID_PARAMETER"
    connect.assert_not_called()


def test_corrected_pool_workflow_uses_code_and_stock_list(monkeypatch):
    session = Session()
    monkeypatch.setattr(cli, "session", session)
    runner = CliRunner()
    for args in (
        ["sector", "create", "-c", "AIPOOL", "-n", "AI突破选股"],
        ["sector", "update-stocks", "-c", "AIPOOL", "-s", "600519.SH,000001.SZ,600487.SH"],
    ):
        result = runner.invoke(cli.cli, args + ["--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["success"] is True
    assert session.raw.calls == [
        ("create_sector", (), {"block_code": "AIPOOL", "block_name": "AI突破选股"}),
        ("send_user_block", (), {"block_code": "AIPOOL", "stock_list": ["600519.SH", "000001.SZ", "600487.SH"], "show": True}),
    ]


def test_corrected_trading_statistics_passes_target_and_fields(monkeypatch):
    session = Session()
    monkeypatch.setattr(cli, "session", session)
    result = CliRunner().invoke(cli.cli, [
        "financial", "trading", "--type", "stock", "--target", "600519.SH",
        "-f", "GP1", "--json",
    ])
    assert result.exit_code == 0, result.output
    assert session.raw.calls[-1] == (
        "get_gpjy_value", (), {"stock_list": ["600519.SH"], "field_list": ["GP1"], "start_time": "", "end_time": ""},
    )


def _leaf_paths(command, prefix=""):
    if isinstance(command, click.Group):
        for name, child in command.commands.items():
            yield from _leaf_paths(child, f"{prefix} {name}".strip())
    else:
        yield prefix


def _readme():
    return (Path(__file__).resolve().parents[3] / "README.md").read_text(encoding="utf-8")


def test_readme_has_a_reference_for_every_leaf_command():
    documented = set()
    for group, names in re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", _readme(), re.MULTILINE):
        if group == "全局":
            documented.update(re.findall(r"[a-z][a-z-]*", names))
        elif group in cli.cli.commands and isinstance(cli.cli.commands[group], click.Group):
            documented.update(f"{group} {name}" for name in re.findall(r"[a-z][a-z-]*", names))
    assert documented == set(_leaf_paths(cli.cli))


def test_readme_shell_examples_parse_without_invoking_callbacks(monkeypatch):
    # Parsing validates option names and required values; never run writes/trades.
    connect = Mock(side_effect=AssertionError("Documentation parsing must stay offline"))
    monkeypatch.setattr(cli.session, "ensure_initialized", connect)
    checked = 0
    for block in re.findall(r"~~~powershell\n(.*?)~~~", _readme(), re.DOTALL):
        for line in block.splitlines():
            match = re.match(r"(?:uv run (?:--locked )?)?(?:tdx-tq|cli-anything-tdx-tq) (.+)$", line)
            if not match:
                continue
            args = shlex.split(match.group(1))
            command = cli.cli
            try:
                with command.make_context("tdx-tq", args) as context:
                    while isinstance(command, click.Group):
                        remaining = context._protected_args + context.args
                        name, command, remaining = command.resolve_command(context, remaining)
                        context = command.make_context(name, remaining, parent=context)
            except click.exceptions.Exit as exc:
                assert exc.exit_code == 0, line
            checked += 1
    assert checked >= 8
    connect.assert_not_called()


@pytest.mark.parametrize("stdout", ["{}", "[]", "TQ initialized\n{}"])
def test_readonly_probe_does_not_pass_empty_or_noisy_results(stdout, monkeypatch):
    from scripts import verify_readme_commands as audit
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr=""))
    result = audit.probe(("stock", "sample", ["stock", "kzz", "-s", "113031.SH", "--json"], 0), 1)
    assert result["matched_expectation"] is False


def test_readonly_probe_records_timeout_without_claiming_sdk_rejection(monkeypatch):
    from scripts import verify_readme_commands as audit
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(audit.subprocess, "run", timeout)
    result = audit.probe(("market", "sample", ["market", "zdt", "-s", "600519.SH", "--json"], 0), 1)
    assert result["timed_out"] is True
    assert result["matched_expectation"] is False
    assert "exit_code" not in result
