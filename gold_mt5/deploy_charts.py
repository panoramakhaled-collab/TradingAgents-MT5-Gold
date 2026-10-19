"""Explicit chart-only deployment; separate from the public read-only gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CHART_ONLY = frozenset(
    {
        "list_open_charts",
        "chart_open",
        "chart_add_indicator",
        "chart_remove_indicator",
        "chart_get_indicator_state",
        "chart_save_template",
    }
)
FRAMES = ("M5", "M15", "H1", "H4", "D1")
NAME = "Gold Advanced Structure v2"
BASE_INDICATORS = (
    ("Moving Average", "period=20,ma_shift=0,method=1,applied_price=0", "MA(20)"),
    ("Moving Average", "period=50,ma_shift=0,method=1,applied_price=0", "MA(50)"),
    ("Moving Average", "period=200,ma_shift=0,method=1,applied_price=0", "MA(200)"),
    ("Bollinger Bands", "period=20,bands_shift=0,deviation=2,applied_price=0", "Bands(20)"),
    ("Relative Strength Index", "period=14,applied_price=0", "RSI(14)"),
    ("MACD", "fast_ema=12,slow_ema=26,macd_sma=9,applied_price=0", "MACD(12,26,9)"),
    ("Average True Range", "period=14", "ATR(14)"),
)


async def deploy(gateway: Path, terminal: Path, compiled: Path, reload=False):
    sys.path.insert(0, str(gateway.resolve()))
    from server import UPSTREAM_URL, account_is_expected, target_account, token

    destination = terminal / "MQL5/Indicators/Khaled Gold/Khaled_Gold_Advanced.ex5"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.copy2(destination, destination.with_suffix(".ex5.bak"))
    shutil.copy2(compiled, destination)
    shutil.copy2(
        Path(__file__).parent / "mt5/Khaled_Gold_Advanced.mq5", destination.with_suffix(".mq5")
    )
    expected = target_account()
    async with (
        httpx.AsyncClient(headers={"Authorization": "Bearer " + token()}, timeout=30) as http,
        streamable_http_client(UPSTREAM_URL, http_client=http) as (rd, wr, _),
        ClientSession(rd, wr) as session,
    ):
        await session.initialize()

        async def call(name, args):
            if name not in CHART_ONLY:
                raise ValueError("Only chart operations are permitted")
            account = await session.call_tool("get_trading_account_info", {})
            if not account_is_expected(account, *expected):
                raise RuntimeError("Selected account guard rejected chart setup")
            result = await session.call_tool(name, args)
            if result.isError:
                detail = next((b.text for b in result.content if hasattr(b, "text")), "")
                raise RuntimeError(f"Chart action failed: {name}: {detail[:1000]}")
            return json.loads(result.content[0].text)

        listed = await call("list_open_charts", {})
        for f in FRAMES:
            if not any(c["symbol"] == "XAUUSD" and c["period"] == f for c in listed["charts"]):
                await call("chart_open", {"symbol": "XAUUSD", "period": f})
        listed = await call("list_open_charts", {})
        backup = datetime.now().strftime("%Y%m%d_%H%M%S")
        charts = [c for c in listed["charts"] if c["symbol"] == "XAUUSD" and c["period"] in FRAMES]
        for n, chart in enumerate(charts):
            cid, frame = str(chart["chart_id"]), chart["period"]
            await call(
                "chart_save_template",
                {
                    "chart_id": cid,
                    "template_filename": f"Gold_before_v2_{backup}_{frame}_{n}.tpl",
                    "overwrite": False,
                },
            )
            present = {i["short_name"] for i in chart.get("indicators", [])}
            for base_name, parameters, short_name in BASE_INDICATORS:
                if short_name not in present:
                    await call(
                        "chart_add_indicator",
                        {
                            "chart_id": cid,
                            "indicator_name": base_name,
                            "indicator_parameters": parameters,
                        },
                    )
            existing = next(
                (i for i in chart.get("indicators", []) if i["short_name"] == NAME), None
            )
            if existing and reload:
                await call(
                    "chart_remove_indicator",
                    {"chart_id": cid, "window": existing["window"], "short_name": NAME},
                )
            if not existing or reload:
                await call(
                    "chart_add_indicator",
                    {
                        "chart_id": cid,
                        "indicator_name": "Khaled_Gold_Advanced",
                        "custom_indicator_path": r"C:\Program Files\MetaTrader 5\MQL5\Indicators\Khaled Gold\Khaled_Gold_Advanced.ex5",
                        "indicator_parameters": "PivotStrength=2,ScanBars=300,GapMaxAge=36,MaxGaps=6,MinGapATR=0.05,DisplacementBodyATR=0.8,ShowStructure=true,ShowLiquidity=true,ShowSwingFib=true,ShowGaps=true,ShowActivityProxy=true",
                    },
                )
            verified = False
            for _ in range(20):
                current = await call("list_open_charts", {})
                updated = next(c for c in current["charts"] if str(c["chart_id"]) == cid)
                indicator = next(
                    (i for i in updated.get("indicators", []) if i["short_name"] == NAME),
                    None,
                )
                if indicator:
                    state = await call(
                        "chart_get_indicator_state",
                        {
                            "chart_id": cid,
                            "window": indicator["window"],
                            "short_name": NAME,
                        },
                    )
                    found = {i["short_name"] for i in updated.get("indicators", [])}
                    if (
                        state.get("ok")
                        and state["state"]["indicator"].get("calculated_bars", 0) > 0
                        and {i[2] for i in BASE_INDICATORS}.issubset(found)
                    ):
                        verified = True
                        break
                await asyncio.sleep(0.25)
            if not verified:
                raise RuntimeError(f"Advanced indicator not calculating on {frame}")
            for old in chart.get("indicators", []):
                if old["short_name"] == "Gold Fib20 + FVG50":
                    await call(
                        "chart_remove_indicator",
                        {
                            "chart_id": cid,
                            "window": old["window"],
                            "short_name": old["short_name"],
                        },
                    )
            await call(
                "chart_save_template",
                {
                    "chart_id": cid,
                    "template_filename": f"Khaled_Gold_Advanced_v2_{frame}.tpl",
                    "overwrite": True,
                },
            )
            print(
                f"PASS {frame}: advanced overlay calculating; original template backed up",
                flush=True,
            )
        print("CHARTS VERIFIED; no trading operations permitted by this script", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", required=True, type=Path)
    p.add_argument("--terminal", required=True, type=Path)
    p.add_argument("--compiled", required=True, type=Path)
    p.add_argument("--reload", action="store_true")
    a = p.parse_args()
    asyncio.run(deploy(a.gateway, a.terminal, a.compiled, a.reload))
