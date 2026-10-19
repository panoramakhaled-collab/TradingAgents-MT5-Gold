"""Point-in-time, read-only XAUUSD data from an explicit MT5 candle export.

The export is written by the separate, allowlisted MT5 gateway. This module
never connects to a terminal, reads credentials, or places orders.
"""

from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.config import get_config

FRAME_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
INDICATORS = (
    "close_20_ema", "close_50_ema", "close_200_ema", "rsi", "macd",
    "macds", "macdh", "atr", "boll", "boll_ub", "boll_lb",
)


def _path() -> Path:
    raw = get_config().get("mt5_bars_path") or os.environ.get("TRADINGAGENTS_MT5_BARS_PATH")
    if not raw:
        raise ValueError("Set TRADINGAGENTS_MT5_BARS_PATH to a verified read-only MT5 candle export")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"MT5 candle export is unavailable: {path}")
    return path


def _verified_export() -> dict:
    path = _path()
    manifest_path = path.with_name("results.json")
    if not manifest_path.is_file():
        raise ValueError("Missing MT5 export provenance manifest (results.json)")
    metadata = json.loads(manifest_path.read_text(encoding="utf-8")).get("metadata", {})
    if (metadata.get("symbol") != "XAUUSD"
            or "read-only gateway" not in metadata.get("source", "")
            or not metadata.get("retrieved_at_utc")
            or not metadata.get("sha256_ohlc_dataset")):
        raise ValueError("MT5 export provenance is incomplete or does not describe read-only XAUUSD")
    export = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(export, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != metadata["sha256_ohlc_dataset"]:
        raise ValueError("MT5 export hash does not match its provenance manifest")
    return export


def _require_gold(symbol: str) -> None:
    if symbol.strip().upper() != "XAUUSD":
        raise ValueError("The MT5 read-only adapter serves the exact XAUUSD symbol only")


def _server_clock() -> bool:
    manifest = json.loads(_path().with_name("results.json").read_text(encoding="utf-8"))
    return manifest.get("metadata", {}).get("clock") == "trade_server"


def load_frame(symbol: str, frame: str, as_of_date: str) -> pd.DataFrame:
    """Return only candles completed at 00:00 on as_of_date in the declared clock.

    Historical daily bars cannot be used on their opening date: the D1 close
    becomes known only at the following midnight. The same rule applies to
    intraday bars and prevents look-ahead in historical graph runs.
    """
    _require_gold(symbol)
    if frame not in FRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe: {frame}")
    server_clock = _server_clock()
    day = datetime.strptime(as_of_date, "%Y-%m-%d")
    if not server_clock:
        day = day.replace(tzinfo=timezone.utc)
    cutoff = day
    export = _verified_export()
    if not isinstance(export, dict) or frame not in export or not isinstance(export[frame], list):
        raise ValueError("MT5 candle export has no requested timeframe")
    rows = []
    last_open = None
    for bar in export[frame]:
        opened = datetime.fromisoformat(bar["time"].replace("Z", "+00:00"))
        if server_clock:
            if opened.tzinfo is not None:
                raise ValueError("Server-clock export must retain naive wall-clock timestamps")
        else:
            if opened.tzinfo is None:
                raise ValueError("MT5 candle timestamps must include a UTC offset")
            opened = opened.astimezone(timezone.utc)
        if last_open is not None and opened <= last_open:
            raise ValueError("MT5 candles must be strictly ordered and unique")
        last_open = opened
        if opened + timedelta(minutes=FRAME_MINUTES[frame]) > cutoff:
            continue
        prices = [float(bar[k]) for k in ("open", "high", "low", "close")]
        if any(not pd.notna(x) or not float("-inf") < x < float("inf") for x in prices):
            raise ValueError("Nonfinite MT5 price")
        if prices[2] > min(prices[0], prices[3]) or prices[1] < max(prices[0], prices[3]):
            raise ValueError("Inconsistent MT5 OHLC candle")
        rows.append({"Date": opened, "Open": prices[0], "High": prices[1],
                     "Low": prices[2], "Close": prices[3],
                     "Volume": int(bar.get("tick_volume", 0)),
                     "Spread": int(bar.get("spread", 0))})
    if not rows:
        raise ValueError(f"No completed {frame} candles on or before {as_of_date}")
    return pd.DataFrame(rows)


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    data = load_frame(symbol, "D1", end_date)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    if not _server_clock():
        start = start.replace(tzinfo=timezone.utc)
    data = data[data["Date"] >= start]
    if data.empty:
        raise ValueError("No completed D1 candles in requested MT5 date range")
    clock = "MT5 server clock" if _server_clock() else "UTC (legacy export assumption)"
    return (f"# MT5 read-only XAUUSD completed D1 candles, as of {end_date} 00:00 {clock}\n"
            "# Volume means tick activity, not exchange-traded volume.\n"
            + data.to_csv(index=False))


def get_indicator(symbol: str, indicator: str, as_of_date: str, look_back_days: int) -> str:
    if indicator not in INDICATORS:
        raise ValueError(f"Unsupported MT5 indicator: {indicator}")
    data = load_frame(symbol, "D1", as_of_date)
    values = wrap(data.drop(columns=["Spread"]).copy())
    values[indicator]
    out = pd.DataFrame({"Date": data["Date"], indicator: values[indicator]})
    since = datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=look_back_days)
    if not _server_clock():
        since = since.replace(tzinfo=timezone.utc)
    return f"# {indicator} from completed MT5 D1 candles\n" + out[out["Date"] >= since].to_csv(index=False)


def build_verified_market_snapshot(symbol: str, as_of_date: str, look_back_days: int = 30) -> str:
    _require_gold(symbol)
    clock = "MT5 server clock" if _server_clock() else "UTC (legacy export assumption)"
    lines = [f"## MT5 read-only XAUUSD verified snapshot as of {as_of_date} 00:00 {clock}",
             "Only completed candles are included. No Yahoo/GC=F substitution."]
    for frame in ("D1", "H4", "H1", "M15"):
        data = load_frame(symbol, frame, as_of_date)
        values = wrap(data.drop(columns=["Spread"]).copy())
        last = data.iloc[-1]
        lines.append(f"### {frame}: last completed open {last['Date'].isoformat()}, close {last['Close']:.2f}, "
                     f"high {last['High']:.2f}, low {last['Low']:.2f}, spread points {last['Spread']}")
        for name in INDICATORS:
            try:
                value = float(values[name].iloc[-1])
                lines.append(f"- {name}: {value:.3f}" if pd.notna(value) else f"- {name}: N/A")
            except (KeyError, ValueError, TypeError):
                lines.append(f"- {name}: N/A")
        recent = data.tail(min(max(1, look_back_days), 20))
        lines.append("- Recent completed closes: " + ", ".join(
            f"{row.Date:%Y-%m-%d %H:%M}={row.Close:.2f}" for row in recent.itertuples()))
    lines.append("Historical snapshot only. No verified live quote or event feed is implied.")
    lines.append(build_advanced_structure_snapshot(symbol, as_of_date))
    return "\n".join(lines)


def build_advanced_structure_snapshot(symbol: str, as_of_date: str) -> str:
    """Expose causal structure facts to the existing TradingAgents market analyst."""
    _require_gold(symbol)
    export = _verified_export()
    if not _server_clock() or not all(f in export for f in ("M5", "M15", "H1")):
        return "Advanced structure unavailable: verified server-clock M5 export required."
    from gold_mt5.advanced import Context, scan, validate_bars

    cutoff = datetime.strptime(as_of_date, "%Y-%m-%d")
    frames = {f: validate_bars(b, f, cutoff) for f, b in export.items() if f in FRAME_MINUTES}
    if not frames["M5"]:
        return "Advanced structure unavailable: no completed M5 history at this date."
    result = scan(frames["M5"], context=Context(frames).at)
    latest = result["latest"]
    facts = {"version": result["version"], "last_completed_M5": latest["bar"].get("time"),
             "confirmed_swing_high": latest["swing_high"], "confirmed_swing_low": latest["swing_low"],
             "structure_direction": latest["structure_direction"], "swing_fib": latest["swing_fibonacci"],
             "active_FVG": latest["active_gaps"][-5:], "recent_events": result["events"][-8:],
             "latest_qualified_signal": result["signals"][-1:] or None,
             "tick_weighted_price_proxy": latest["bar"].get("tick_weighted_price_proxy"),
             "real_orderflow": latest["real_orderflow"]}
    return "### Advanced causal structure (facts, not a live trade)\n" + json.dumps(facts, ensure_ascii=False)
