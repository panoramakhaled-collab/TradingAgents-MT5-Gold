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

FRAME_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}
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


def load_frame(symbol: str, frame: str, as_of_date: str) -> pd.DataFrame:
    """Return only candles completed by the END of as_of_date in UTC.

    Historical daily bars cannot be used on their opening date: the D1 close
    becomes known only at the following midnight. The same rule applies to
    intraday bars and prevents look-ahead in historical graph runs.
    """
    _require_gold(symbol)
    if frame not in FRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe: {frame}")
    day = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cutoff = day + timedelta(days=1)
    export = _verified_export()
    if not isinstance(export, dict) or frame not in export or not isinstance(export[frame], list):
        raise ValueError("MT5 candle export has no requested timeframe")
    rows = []
    last_open = None
    for bar in export[frame]:
        opened = datetime.fromisoformat(bar["time"].replace("Z", "+00:00"))
        if opened.tzinfo is None:
            raise ValueError("MT5 candle timestamps must include a UTC offset")
        opened = opened.astimezone(timezone.utc)
        if last_open is not None and opened <= last_open:
            raise ValueError("MT5 candles must be strictly ordered and unique")
        last_open = opened
        if opened + timedelta(minutes=FRAME_MINUTES[frame]) >= cutoff:
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
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    data = data[data["Date"] >= start]
    if data.empty:
        raise ValueError("No completed D1 candles in requested MT5 date range")
    return (f"# MT5 read-only XAUUSD completed D1 candles, as of {end_date} UTC\n"
            "# Tick volume is absent in this export; Volume=0 is not exchange volume.\n"
            + data.to_csv(index=False))


def get_indicator(symbol: str, indicator: str, as_of_date: str, look_back_days: int) -> str:
    if indicator not in INDICATORS:
        raise ValueError(f"Unsupported MT5 indicator: {indicator}")
    data = load_frame(symbol, "D1", as_of_date)
    values = wrap(data.drop(columns=["Spread"]).copy())
    values[indicator]
    out = pd.DataFrame({"Date": data["Date"], indicator: values[indicator]})
    since = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(days=look_back_days)
    return f"# {indicator} from completed MT5 D1 candles\n" + out[out["Date"] >= since].to_csv(index=False)


def build_verified_market_snapshot(symbol: str, as_of_date: str, look_back_days: int = 30) -> str:
    _require_gold(symbol)
    lines = [f"## MT5 read-only XAUUSD verified snapshot as of {as_of_date} UTC",
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
    return "\n".join(lines)
