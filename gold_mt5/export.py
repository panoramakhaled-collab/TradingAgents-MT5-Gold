"""Export through the existing account-guarded read-only gateway only."""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}


async def read(session, operation, arguments):
    result = await session.call_tool(
        "call_readonly_operation",
        {
            "operation": operation,
            "arguments": arguments,
        },
    )
    if result.isError:
        raise RuntimeError(f"Read-only export failed: {operation}")
    envelope = result.structuredContent or json.loads(result.content[0].text)
    if envelope.get("isError"):
        raise RuntimeError(f"MT5 rejected read: {operation}")
    return json.loads(envelope["content"][0]["text"])


async def export(gateway: Path, output: Path, start: str, daily_start: str):
    params = StdioServerParameters(
        command=str(gateway / ".venv/bin/python"), args=[str(gateway / "server.py")]
    )
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()
        clock = await read(session, "get_time_information", {})
        now = datetime.fromisoformat(clock["trade_server_last_known_time"])
        data = {}
        for frame, minutes in MINUTES.items():
            request_start = daily_start if frame == "D1" else start
            payload = await read(
                session,
                "get_chart_history",
                {
                    "datetime_from": request_start,
                    "datetime_to": now.isoformat(),
                    "symbol": "XAUUSD",
                    "period": frame,
                    "limit": 100000,
                },
            )
            if (
                not payload.get("ok")
                or payload.get("symbol") != "XAUUSD"
                or payload.get("period") != frame
            ):
                raise RuntimeError("Export identity mismatch")
            bars = [
                b
                for b in payload["history"]
                if datetime.fromisoformat(b["time"]) + timedelta(minutes=minutes) <= now
            ]
            times = [b["time"] for b in bars]
            if not bars or times != sorted(set(times)) or len(bars) >= 100000:
                raise RuntimeError(f"Invalid or truncated {frame} data")
            data[frame] = bars
            print(frame, len(bars), times[0], times[-1], flush=True)
        output.mkdir(parents=True, exist_ok=True)
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        (output / "gold-mt5-bars.json").write_text(json.dumps(data), encoding="utf-8")
        metadata = {
            "source": "selected demo via account-guarded read-only gateway",
            "symbol": "XAUUSD",
            "retrieved_at_utc": clock["utc_time"],
            "clock": "trade_server",
            "trade_server_time": now.isoformat(),
            "historical_utc_offsets": "not independently verified; timestamps retained as server wall clock",
            "sha256_ohlc_dataset": hashlib.sha256(canonical).hexdigest(),
            "real_trade_volume_available": any(b.get("real_volume", 0) > 0 for b in data["M5"]),
            "tick_activity_available": any(b.get("tick_volume", 0) > 0 for b in data["M5"]),
            "quote_point": 0.01,
        }
        (output / "results.json").write_text(json.dumps({"metadata": metadata}, indent=2))
        print("EXPORT VERIFIED", metadata["sha256_ohlc_dataset"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--start", default="2026-01-01T00:00:00")
    p.add_argument("--daily-start", default="2024-01-01T00:00:00")
    a = p.parse_args()
    asyncio.run(export(a.gateway.resolve(), a.out, a.start, a.daily_start))
