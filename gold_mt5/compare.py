"""Matched historical evaluation of legacy entries and causal structure entries."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from gold_mt5.advanced import Context, Rules, VERSION, features, scan, stamp, validate_bars
from gold_mt5.backtest import _features as legacy_features, _signal as legacy_signal


def load_export(path: Path) -> tuple[dict, dict]:
    metadata = json.loads(path.with_name("results.json").read_text())["metadata"]
    data = json.loads(path.read_text())
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if (metadata.get("symbol") != "XAUUSD" or metadata.get("clock") != "trade_server"
            or "read-only gateway" not in metadata.get("source", "")
            or hashlib.sha256(canonical).hexdigest() != metadata.get("sha256_ohlc_dataset")):
        raise ValueError("Unverified server-clock MT5 export")
    cutoff = stamp(metadata["trade_server_time"])
    return {f: validate_bars(b, f, cutoff) for f, b in data.items()}, metadata


def legacy_entries(frames: dict) -> list[dict]:
    renamed = {"time": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close", "spread": "Spread"}
    data = {}
    for f in ("M15", "H1", "H4"):
        d = pd.DataFrame(frames[f]).rename(columns=renamed)
        d["Date"] = pd.to_datetime(d["Date"])
        data[f] = legacy_features(d)
    m, h, t = data["M15"], data["H1"], data["H4"]
    hs, ts = h.Date.tolist(), t.Date.tolist()
    result = []
    for i in range(201, len(m)):
        at = m.iloc[i].Date + timedelta(minutes=15)
        j = bisect_right(hs, at - timedelta(hours=1)) - 1
        k = bisect_right(ts, at - timedelta(hours=4)) - 1
        if j < 200 or k < 200:
            continue
        side = legacy_signal(m, h, t, i, j, k)
        if side:
            result.append({"signal_time": at.isoformat(), "side": side,
                           "atr": float(m.iloc[i].atr), "source_index": i})
    return result


def execute(bars: list[dict], signals: list[dict], start: datetime, end: datetime,
            *, frame_minutes=5, max_hold=24, target_rs=(1., 2., 3.), structure=False) -> list[dict]:
    rows = features(bars)
    times = [stamp(b["time"]) for b in bars]
    results, free_after = [], datetime.min
    for signal in signals:
        at = stamp(signal["signal_time"])
        if not start <= at < end or at < free_after:
            continue
        entry_i = bisect_left(times, at)
        if entry_i >= len(rows) or entry_i == 0 or times[entry_i] != at:
            continue  # Never invent an entry inside a closed-session gap.
        entry = rows[entry_i]["open"]
        side = signal["side"]
        atr = rows[entry_i - 1]["atr"]
        if not atr or atr <= 0:
            continue
        stop = signal["structure_stop"] if structure else entry - side * 1.5 * atr
        risk = side * (entry - stop)
        if risk <= 0 or (structure and not 0.7 * atr <= risk <= 3.5 * atr):
            continue
        targets = [entry + side * r * risk for r in target_rs]
        hit = [False] * len(targets)
        remaining, gross_r = 1., 0.
        weight = 1. / len(targets)
        last_i = entry_i
        reason = "time"
        mae, mfe = 0., 0.
        for i in range(entry_i, min(len(rows), entry_i + max_hold)):
            b = rows[i]
            if times[i] >= end:
                reason = "period_end"
                break
            last_i = i
            adverse = side * ((b["low"] if side == 1 else b["high"]) - entry) / risk
            favorable = side * ((b["high"] if side == 1 else b["low"]) - entry) / risk
            mae, mfe = min(mae, adverse), max(mfe, favorable)
            stop_hit = b["low"] <= stop if side == 1 else b["high"] >= stop
            if stop_hit:
                stop_fill = min(stop, b["open"]) if side == 1 else max(stop, b["open"])
                gross_r += remaining * side * (stop_fill - entry) / risk
                remaining, reason = 0., "stop"
                break  # Stop takes precedence if intrabar order is ambiguous.
            for k, target in enumerate(targets):
                target_hit = b["high"] >= target if side == 1 else b["low"] <= target
                if not hit[k] and target_hit:
                    gross_r += weight * target_rs[k]
                    remaining -= weight
                    hit[k] = True
            if remaining < 1e-8:
                remaining, reason = 0., "all_targets"
                break
        if remaining:
            gross_r += remaining * side * (rows[last_i]["close"] - entry) / risk
        # One fixed historical cost scenario for every arm; no account money is implied.
        cost = (max(0, bars[entry_i].get("spread", 0)) + max(0, bars[last_i].get("spread", 0))) * .01 / 2 + .10
        net_r = gross_r - cost / risk
        free_after = times[last_i] + timedelta(minutes=frame_minutes)
        results.append({"signal_time_server": at.isoformat(), "entry_time_server": times[entry_i].isoformat(),
                        "exit_time_server": free_after.isoformat(), "side": "buy" if side == 1 else "sell",
                        "entry": entry, "stop": stop, "tp1": targets[0],
                        "tp2": targets[1] if len(targets) > 1 else None,
                        "tp3": targets[2] if len(targets) > 2 else None,
                        "risk_price_units": risk, "gross_r": gross_r, "net_r": net_r,
                        "target_hits": str(hit), "exit_reason": reason,
                        "bar_mae_upper_bound_r": mae, "bar_mfe_upper_bound_r": mfe,
                        "regime": signal.get("volatility_regime", rows[entry_i - 1]["volatility_regime"])})
    return results


def stats(trades: list[dict]) -> dict:
    rs = [t["net_r"] for t in trades]
    if not rs:
        return {"trades": 0, "total_r": 0., "mean_r": None, "profit_factor": None,
                "win_rate": None, "max_drawdown_r": 0., "expectancy_bootstrap_95": None}
    equity = peak = drawdown = 0.
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r < 0]
    rng = random.Random(20261020)
    means = sorted(statistics.fmean(rng.choices(rs, k=len(rs))) for _ in range(1000))
    return {"trades": len(rs), "total_r": round(sum(rs), 3), "mean_r": round(statistics.fmean(rs), 4),
            "win_rate": round(len(wins) / len(rs), 4),
            "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
            "max_drawdown_r": round(drawdown, 3),
            "expectancy_bootstrap_95": [round(means[25], 4), round(means[974], 4)],
            "bootstrap_caveat": "trade resampling ignores serial dependence; diagnostic only"}


def grouped(trades: list[dict], key) -> dict:
    groups = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade)
    return {k: stats(v) for k, v in groups.items()}


def run(path: Path, out: Path, start="2026-04-20", end="2026-10-19") -> dict:
    frames, meta = load_export(path)
    start_at, end_at = datetime.fromisoformat(start), datetime.fromisoformat(end) + timedelta(days=1)
    split = datetime(2026, 7, 20)
    ctx = Context(frames)
    advanced = scan(frames["M5"], context=ctx.at)
    legacy = legacy_entries(frames)
    arms = {
        "legacy_original_M15": execute(frames["M15"], legacy, start_at, end_at,
            frame_minutes=15, max_hold=32, target_rs=(1.5,)),
        "legacy_matched_M5": execute(frames["M5"], legacy, start_at, end_at),
        "advanced_matched_M5": execute(frames["M5"], advanced["signals"], start_at, end_at),
        "advanced_structure_stop": execute(frames["M5"], advanced["signals"], start_at, end_at, structure=True),
    }
    result = {"version": VERSION, "start_server_date": start, "end_server_date": end,
              "split_server_date": split.isoformat(), "rules": advanced["rules"], "provenance": meta,
              "prespecified_entry_rule_count": 1,
              "assumptions": {"matched_exit_targets_R": [1, 2, 3], "target_weights": [1/3]*3,
                              "matched_hold_M5_bars": 24, "matched_ATR_stop_multiple": 1.5,
                              "same_bar_stop_first": True, "break_even_stop": False,
                              "bar_excursions_are_upper_bounds": True,
                              "historical_ticks_commissions_swaps": "not available",
                              "true_forward_test": False,
                              "real_orderflow_delta": "not available; tick activity proxy labelled separately"},
              "data_quality": {}, "arms": {}}
    for f, bars in frames.items():
        times = [stamp(b["time"]) for b in bars]
        gaps = [(b-a).total_seconds()/60 for a,b in zip(times,times[1:])]
        result["data_quality"][f] = {"bars": len(bars), "first": bars[0]["time"], "last": bars[-1]["time"],
            "duplicate_count": len(times)-len(set(times)), "session_or_missing_bar_gaps": sum(g > {"M5":5,"M15":15,"H1":60,"H4":240,"D1":1440}[f] for g in gaps)}
    out.mkdir(parents=True, exist_ok=True)
    for name, trades in arms.items():
        result["arms"][name] = {"all": stats(trades),
            "earlier": stats([t for t in trades if stamp(t["entry_time_server"]) < split]),
            "later": stats([t for t in trades if stamp(t["entry_time_server"]) >= split]),
            "monthly": grouped(trades, lambda t: t["entry_time_server"][:7]),
            "regimes": grouped(trades, lambda t: t["regime"]),
            "server_hour": grouped(trades, lambda t: t["entry_time_server"][11:13])}
        with (out / f"{name}.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(trades[0]) if trades else ["signal_time_server"])
            writer.writeheader()
            writer.writerows(trades)
    (out / "comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out / "signal_proofs.json").write_text(json.dumps(advanced["signals"], indent=2))
    (out / "candidate_rejections.json").write_text(json.dumps(advanced["candidates"], indent=2))
    print(json.dumps({name: {"all": x["all"], "later": x["later"]} for name, x in result["arms"].items()}, indent=2))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bars", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--start", default="2026-04-20")
    p.add_argument("--end", default="2026-10-19")
    args = p.parse_args()
    run(args.bars, args.out, args.start, args.end)
