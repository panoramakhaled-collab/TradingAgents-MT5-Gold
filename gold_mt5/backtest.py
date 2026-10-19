"""Six-month, deterministic MT5 XAUUSD execution proxy for the gold research flow.

This evaluates a fixed market/momentum/risk rule set on historical candles.
It does NOT claim to be an LLM-agent backtest or an MT5 Strategy Tester result.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from gold_mt5.config import gold_config
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.vendors.mt5_readonly import load_frame

POINT_USD = 0.01  # Scenario assumption for the cached demo quote format.
SLIPPAGE_USD_ROUND_TRIP = 0.10  # Explicit scenario assumption, not a measured fill.
MAX_HOLD_BARS = 32  # Eight hours on M15.


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["Close"]
    for period in (20, 50, 200):
        out[f"ema{period}"] = close.ewm(span=period, adjust=False, min_periods=period).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    prev = close.shift(1)
    tr = pd.concat(
        [out["High"] - out["Low"], (out["High"] - prev).abs(), (out["Low"] - prev).abs()], axis=1
    ).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    out["macdh"] = macd - macd.ewm(span=9, adjust=False).mean()
    return out


def _completed_index(times: list[datetime], minutes: int, at: datetime) -> int:
    return bisect_right(times, at - timedelta(minutes=minutes)) - 1


def _signal(m15: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame, i: int, j: int, k: int) -> int:
    bar, prev, hour, trend = m15.iloc[i], m15.iloc[i - 1], h1.iloc[j], h4.iloc[k]
    needed = [
        bar.ema20,
        bar.ema50,
        bar.rsi,
        bar.atr,
        bar.macdh,
        hour.ema20,
        hour.ema50,
        hour.rsi,
        trend.ema20,
        trend.ema50,
        trend.ema200,
    ]
    if any(pd.isna(v) for v in needed):
        return 0
    long = (
        trend.Close > trend.ema200
        and trend.ema20 > trend.ema50
        and hour.ema20 > hour.ema50
        and 50 < hour.rsi < 72
        and bar.ema20 > bar.ema50
        and prev.Close <= prev.ema20
        and bar.Close > bar.ema20
        and 50 < bar.rsi < 72
        and bar.macdh > 0
    )
    short = (
        trend.Close < trend.ema200
        and trend.ema20 < trend.ema50
        and hour.ema20 < hour.ema50
        and 28 < hour.rsi < 50
        and bar.ema20 < bar.ema50
        and prev.Close >= prev.ema20
        and bar.Close < bar.ema20
        and 28 < bar.rsi < 50
        and bar.macdh < 0
    )
    return 1 if long else -1 if short else 0


def run(bars_path: str, start: str, end: str) -> dict:
    start_at = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_at = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    with run_config(gold_config(bars_path, ".")):
        cutoff_day = end_at.strftime("%Y-%m-%d")
        frames = {f: _features(load_frame("XAUUSD", f, cutoff_day)) for f in ("M15", "H1", "H4")}
    m15, h1, h4 = (frames[f] for f in ("M15", "H1", "H4"))
    hour_times, trend_times = h1.Date.tolist(), h4.Date.tolist()
    trades = []
    i = 201
    while i < len(m15) - 1:
        current = m15.iloc[i]
        signal_time = current.Date + timedelta(minutes=15)
        if not start_at <= signal_time < end_at:
            i += 1
            continue
        j = _completed_index(hour_times, 60, signal_time)
        k = _completed_index(trend_times, 240, signal_time)
        if j < 200 or k < 200:
            i += 1
            continue
        side = _signal(m15, h1, h4, i, j, k)
        if not side:
            i += 1
            continue
        entry_i = i + 1
        entry = m15.iloc[entry_i]
        if entry.Date >= end_at:
            break
        risk_usd = 1.5 * float(current.atr)
        if not math.isfinite(risk_usd) or risk_usd <= 0:
            i += 1
            continue
        entry_price = float(entry.Open)
        stop = entry_price - side * risk_usd
        target = entry_price + side * 1.5 * risk_usd
        max_exit = min(len(m15) - 1, entry_i + MAX_HOLD_BARS - 1)
        exit_i, reason, exit_price = max_exit, "time", float(m15.iloc[max_exit].Close)
        for x in range(entry_i, max_exit + 1):
            candle = m15.iloc[x]
            if candle.Date >= end_at:
                exit_i, reason, exit_price = x - 1, "period_end", float(m15.iloc[x - 1].Close)
                break
            stop_hit = candle.Low <= stop if side == 1 else candle.High >= stop
            target_hit = candle.High >= target if side == 1 else candle.Low <= target
            if stop_hit:  # Conservative when both thresholds fall within one candle.
                exit_i, reason, exit_price = x, "stop", stop
                break
            if target_hit:
                exit_i, reason, exit_price = x, "target", target
                break
        exit_bar = m15.iloc[exit_i]
        entry_spread = max(0, float(entry.Spread)) * POINT_USD
        exit_spread = max(0, float(exit_bar.Spread)) * POINT_USD
        costs = (entry_spread + exit_spread) / 2 + SLIPPAGE_USD_ROUND_TRIP
        gross = side * (exit_price - entry_price)
        net = gross - costs
        trades.append(
            {
                "signal_utc": signal_time.isoformat(),
                "entry_utc": entry.Date.isoformat(),
                "exit_utc": exit_bar.Date.isoformat(),
                "side": "long" if side == 1 else "short",
                "entry_reference": round(entry_price, 3),
                "exit_reference": round(exit_price, 3),
                "stop_reference": round(stop, 3),
                "target_reference": round(target, 3),
                "risk_usd_per_oz": round(risk_usd, 3),
                "costs_usd_per_oz": round(costs, 3),
                "gross_r": round(gross / risk_usd, 6),
                "net_r": round(net / risk_usd, 6),
                "exit_reason": reason,
            }
        )
        i = exit_i + 1  # One position at a time, no overlapping trades.
    return {
        "start": start,
        "end": end,
        "source": "cached MT5 read-only XAUUSD OHLC candles",
        "model": "fixed H4/H1/M15 market-momentum-risk proxy, no LLM calls",
        "assumptions": {
            "point_usd": POINT_USD,
            "round_trip_slippage_usd_per_oz": SLIPPAGE_USD_ROUND_TRIP,
            "stop_atr_multiple": 1.5,
            "target_r_multiple": 1.5,
            "max_hold_m15_bars": MAX_HOLD_BARS,
            "stop_first_when_both_hit": True,
        },
        "trades": trades,
    }


def _stats(trades: list[dict]) -> dict:
    rs = [t["net_r"] for t in trades]
    if not rs:
        return {"trades": 0}
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    return {
        "trades": len(rs),
        "win_rate": round(len(wins) / len(rs), 4),
        "mean_net_r": round(sum(rs) / len(rs), 4),
        "total_net_r": round(sum(rs), 3),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
        "max_drawdown_r": round(drawdown, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", required=True)
    parser.add_argument("--start", default="2026-04-20")
    parser.add_argument("--end", default="2026-10-19")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.bars, args.start, args.end)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trades = result.pop("trades")
    midpoint = (
        datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
        + (
            datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
            - datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
        )
        / 2
    )
    result["all"] = _stats(trades)
    result["first_half"] = _stats(
        [t for t in trades if datetime.fromisoformat(t["entry_utc"]) < midpoint]
    )
    result["second_half"] = _stats(
        [t for t in trades if datetime.fromisoformat(t["entry_utc"]) >= midpoint]
    )
    (out / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (out / "trades.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(trades[0]) if trades else ["signal_utc"])
        writer.writeheader()
        writer.writerows(trades)
    print(
        json.dumps(
            {
                "all": result["all"],
                "first_half": result["first_half"],
                "second_half": result["second_half"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
