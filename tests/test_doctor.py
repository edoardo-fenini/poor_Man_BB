import asyncio

import pytest

from terminal.config import Config
from terminal.doctor import Skip, default_checks, doctor


async def test_doctor_counts_failures_and_reports_each_line(capsys):
    async def ok():
        return "fine"

    async def bad():
        raise RuntimeError("no route")

    async def skipped():
        raise Skip("Set SOME_KEY in .env")

    n = await doctor([("a", ok), ("b", bad), ("c", skipped)])
    out = capsys.readouterr().out
    assert n == 1
    assert "OK   a" in out and "fine" in out
    assert "FAIL b" in out and "no route" in out
    assert "SKIP c" in out and "SOME_KEY" in out
    assert "1 failed" in out


async def test_doctor_times_out_slow_checks(capsys):
    async def slow():
        await asyncio.sleep(60)

    assert await doctor([("slow", slow)], timeout=0.05) == 1
    out = capsys.readouterr().out
    assert "FAIL slow" in out and "timed out" in out


async def test_default_checks_skip_without_keys():
    checks = dict(default_checks(Config("", "", "")))
    for name, key in (("finnhub stream", "FINNHUB_KEY"), ("finnhub news", "FINNHUB_KEY"),
                      ("etherscan", "ETHERSCAN_KEY"), ("sec edgar", "SEC_USER_AGENT")):
        with pytest.raises(Skip, match=key):
            await checks[name]()


def test_default_checks_cover_every_source():
    names = [name for name, _ in default_checks(Config("k", "e", "ua"))]
    assert {"yahoo", "binance", "coinbase", "kraken", "finnhub stream", "finnhub news", "blockchain.info",
            "etherscan", "sec edgar", "gdelt"} <= set(names)
    assert sum(name.startswith("rss: ") for name in names) >= 12 and len(names) == len(set(names))


def test_cli_rejects_unknown_command(capsys):
    from terminal.__main__ import main
    assert main(["bogus"]) == 2 and "usage" in capsys.readouterr().out
