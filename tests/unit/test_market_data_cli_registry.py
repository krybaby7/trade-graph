"""Candle-store commands plug in by module discovery; unknown commands still fail as before."""

from __future__ import annotations

import argparse
import sys
import types

import pytest

from trade_graph import cli, market_data_cli


def test_existing_commands_still_parse_with_the_registry_in_place() -> None:
    args = cli.build_parser().parse_args(["collect-kraken-history", "--hours", "24"])
    assert args.command == "collect-kraken-history" and args.hours == 24


def test_a_discovered_module_registers_and_dispatches(monkeypatch) -> None:
    fake = types.ModuleType("trade_graph.market_data_cli.fake_probe")

    def register(sub) -> None:
        sub.add_parser("probe-candles").add_argument("--market-database", default="x")

    def run(args, parser):
        return 7 if args.command == "probe-candles" else None

    fake.register, fake.run = register, run
    monkeypatch.setattr(market_data_cli, "_modules", lambda: [fake])
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    market_data_cli.register_all(sub)
    args = parser.parse_args(["probe-candles"])
    assert market_data_cli.dispatch(args, parser) == 7
    assert market_data_cli.dispatch(argparse.Namespace(command="other"), parser) is None


def test_underscore_modules_are_not_commands() -> None:
    names = [module.__name__ for module in market_data_cli._modules()]
    assert not any(name.rsplit(".", 1)[-1].startswith("_") for name in names)


def test_an_unknown_command_still_exits_with_a_usage_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["trade-graph", "no-such-command"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
