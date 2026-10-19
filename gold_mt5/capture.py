"""One verified local MCP snapshot; optional explicit chart annotations only."""
import argparse
import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gold_mt5.write_overlay import write


async def capture(gateway: Path, output: Path, terminal: Path | None = None):
    params = StdioServerParameters(command=str(gateway.resolve()/".venv/bin/python"),
                                   args=[str(gateway.resolve()/"server.py")])
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as session:
            await session.initialize()
            result = await session.call_tool("gold_multitimeframe_snapshot", {"symbol": "XAUUSD"})
            if result.isError:
                raise RuntimeError("Verified read-only snapshot failed; no plan may be inferred")
            snapshot = result.structuredContent or json.loads(result.content[0].text)
    if snapshot.get("advanced", {}).get("version") != "gold-structure-v2.0":
        raise RuntimeError("Advanced module was not loaded by the gateway")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    if terminal:
        write(snapshot, terminal)
    print(json.dumps({"as_of_server": snapshot["trade_server_time"],
                      "bid": snapshot["quote"]["bid"], "ask": snapshot["quote"]["ask"],
                      "advanced_plan": snapshot["advanced"]["plan"]}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--terminal", type=Path)
    a = p.parse_args()
    asyncio.run(capture(a.gateway, a.out, a.terminal))
