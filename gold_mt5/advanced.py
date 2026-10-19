"""Causal gold structure research. No terminal, credentials, orders or LLM calls.

All timestamps are the explicitly declared MT5 server wall clock. Pivots are
known only after their right-hand confirmation bars. Signal proofs are copied
at the signal close; later FVG fills cannot change an earlier decision.
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_right, insort, bisect_left
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable

VERSION = "gold-structure-v2.0"
MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}


@dataclass(frozen=True)
class Rules:
    pivot_strength: int = 2
    sweep_to_break_bars: int = 12
    break_to_gap_bars: int = 6
    gap_max_age: int = 36
    min_gap_atr: float = 0.05
    displacement_body_atr: float = 0.8
    fib_min: float = 0.382
    fib_max: float = 0.786
    structure_buffer_atr: float = 0.15
    min_risk_atr: float = 0.7
    max_risk_atr: float = 3.5


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_bars(bars: list[dict], frame: str, as_of: datetime | None = None) -> list[dict]:
    if frame not in MINUTES or not bars:
        raise ValueError("Missing or unsupported frame")
    clean = []
    previous = None
    for raw in bars:
        opened = stamp(raw["time"])
        if opened.tzinfo is not None:
            raise ValueError("Advanced export must retain naive MT5 server-clock timestamps")
        if previous is not None and opened <= previous:
            raise ValueError("Duplicate or unordered candle")
        previous = opened
        row = dict(raw)
        for key in ("open", "high", "low", "close"):
            row[key] = float(row[key])
            if not math.isfinite(row[key]):
                raise ValueError("Nonfinite candle")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ValueError("Invalid candle range")
        if as_of is None or opened + timedelta(minutes=MINUTES[frame]) <= as_of:
            clean.append(row)
    return clean


def _ema(values: list[float], period: int, alpha: float | None = None) -> list[float]:
    weight = alpha or 2 / (period + 1)
    result = []
    last = values[0]
    for i, value in enumerate(values):
        last = value if i == 0 else last + weight * (value - last)
        result.append(last)
    return result


def features(bars: list[dict]) -> list[dict]:
    if not bars:
        return []
    close = [b["close"] for b in bars]
    emas = {p: _ema(close, p) for p in (12, 20, 26, 50, 200)}
    changes = [0.0] + [close[i] - close[i - 1] for i in range(1, len(close))]
    gain = _ema([max(0, x) for x in changes], 14, 1 / 14)
    loss = _ema([max(0, -x) for x in changes], 14, 1 / 14)
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - close[i - 1]),
            abs(bars[i]["low"] - close[i - 1])) for i in range(1, len(bars))]
    atr = _ema(tr, 14, 1 / 14)
    macd = [a - b for a, b in zip(emas[12], emas[26])]
    signal = _ema(macd, 9)
    rows, atr_window = [], []
    tick_window = []
    day = None
    sum_tick_pv = sum_ticks = sum_real_pv = sum_real = 0.0
    prev_day_high = prev_day_low = None
    day_high, day_low = -math.inf, math.inf
    for i, bar in enumerate(bars):
        row = dict(bar)
        for p in (20, 50, 200):
            row[f"ema{p}"] = emas[p][i] if i >= p - 1 else None
        row["rsi"] = (100 - 100 / (1 + gain[i] / loss[i]) if loss[i] else
                      100.0 if gain[i] else 50.0) if i >= 14 else None
        row["atr"] = atr[i] if i >= 13 else None
        row["macd"], row["macd_signal"], row["macdh"] = macd[i], signal[i], macd[i] - signal[i]
        if i >= 19:
            middle = statistics.fmean(close[i - 19:i + 1])
            dev = statistics.pstdev(close[i - 19:i + 1])
            row["boll"] = {"lower": middle - 2 * dev, "middle": middle, "upper": middle + 2 * dev}
        else:
            row["boll"] = None
        current_day = bar["time"][:10]
        if current_day != day:
            if day is not None:
                prev_day_high, prev_day_low = day_high, day_low
            day, day_high, day_low = current_day, -math.inf, math.inf
            sum_tick_pv = sum_ticks = sum_real_pv = sum_real = 0.0
        row["previous_day_high"], row["previous_day_low"] = prev_day_high, prev_day_low
        day_high, day_low = max(day_high, bar["high"]), min(day_low, bar["low"])
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        ticks, real = float(bar.get("tick_volume", 0)), float(bar.get("real_volume", 0))
        sum_tick_pv += typical * ticks
        sum_ticks += ticks
        sum_real_pv += typical * real
        sum_real += real
        row["tick_weighted_price_proxy"] = sum_tick_pv / sum_ticks if sum_ticks else None
        row["trade_volume_vwap"] = sum_real_pv / sum_real if sum_real else None
        row["relative_tick_activity"] = ticks / statistics.median(tick_window) if tick_window and statistics.median(tick_window) > 0 else None
        tick_window.append(ticks)
        tick_window = tick_window[-20:]
        median_atr = statistics.median(atr_window) if len(atr_window) >= 50 else None
        ratio = atr[i] / median_atr if median_atr else None
        row["volatility_regime"] = "high" if ratio and ratio > 1.5 else "low" if ratio and ratio < 0.67 else "normal" if ratio else "warmup"
        insort(atr_window, atr[i])
        if i >= 200:
            atr_window.pop(bisect_left(atr_window, atr[i - 200]))
        rows.append(row)
    return rows


class Context:
    def __init__(self, frames: dict[str, list[dict]]):
        self.rows = {f: features(b) for f, b in frames.items() if f != "M5"}
        self.ends = {f: [stamp(b["time"]) + timedelta(minutes=MINUTES[f]) for b in rows]
                     for f, rows in self.rows.items()}

    def at(self, when: datetime) -> dict:
        result = {}
        for frame, rows in self.rows.items():
            j = bisect_right(self.ends[frame], when) - 1
            if j >= 0:
                result[frame] = rows[j]
        return result


def fib_levels(low: float, high: float) -> dict:
    return {str(p): round(low + (high - low) * p / 100, 5)
            for p in (0, 23.6, 38.2, 50, 61.8, 78.6, 100)}


def activity_profile(bars: list[dict], lookback=200, bins=24) -> dict:
    sample = bars[-lookback:]
    low, high = min(b["low"] for b in sample), max(b["high"] for b in sample)
    result = {"kind": "tick_activity_proxy", "not_exchange_volume_profile": True,
              "method": "bar typical price weighted by tick count; no intrabar allocation", "bars": len(sample)}
    if high <= low or not any(b.get("tick_volume", 0) > 0 for b in sample):
        return {**result, "available": False}
    width = (high-low)/bins
    weights = [0.] * bins
    for b in sample:
        typical = (b["high"]+b["low"]+b["close"])/3
        bucket = min(bins-1, max(0, int((typical-low)/width)))
        weights[bucket] += max(0, b.get("tick_volume", 0))
    poc = max(range(bins), key=lambda i: weights[i])
    left = right = poc
    accumulated, target = weights[poc], .7*sum(weights)
    while accumulated < target and (left > 0 or right < bins-1):
        l = weights[left-1] if left else -1
        r = weights[right+1] if right < bins-1 else -1
        if r > l:
            right += 1
            accumulated += weights[right]
        else:
            left -= 1
            accumulated += weights[left]
    return {**result, "available": True, "poc": low+(poc+.5)*width,
            "value_area_low": low+left*width, "value_area_high": low+(right+1)*width}


def scan(bars: list[dict], rules: Rules = Rules(),
         context: Callable[[datetime], dict] | None = None) -> dict:
    rows = features(bars)
    active, zones, events, signals, candidates, pivots = [], [], [], [], [], []
    high_pivot = low_pivot = None
    sweeps, breaks, consumed = {}, {}, set()
    last_direction, last_high_break, last_low_break = 0, None, None
    p = rules.pivot_strength
    for i, row in enumerate(rows):
        if i >= 2 * p:
            j = i - p
            surrounding = bars[j - p:j] + bars[j + 1:j + p + 1]
            for side, field in ((-1, "high"), (1, "low")):
                value = bars[j][field]
                valid = all(value > b[field] for b in surrounding) if side == -1 else all(value < b[field] for b in surrounding)
                if valid:
                    pivot = {"origin_index": j, "known_index": i, "time": bars[j]["time"],
                             "confirmed_at": row["time"], "side": side, "price": value}
                    pivots.append(pivot)
                    if side == -1:
                        high_pivot = pivot
                    else:
                        low_pivot = pivot
        if i < 200 or row["atr"] is None:
            continue
        atr = row["atr"]
        # Only levels known at this close are eligible liquidity references.
        references = []
        if high_pivot:
            references.append((-1, high_pivot["price"], "confirmed_swing"))
        if low_pivot:
            references.append((1, low_pivot["price"], "confirmed_swing"))
        if row["previous_day_high"] is not None:
            references += [(-1, row["previous_day_high"], "previous_day"),
                           (1, row["previous_day_low"], "previous_day")]
        sweep_hits = []
        for side, level, source in references:
            swept = row["high"] > level > row["close"] if side == -1 else row["low"] < level < row["close"]
            if swept:
                sweep_hits.append({"index": i, "time": row["time"], "side": side,
                                   "level": level, "extreme": row["high"] if side == -1 else row["low"],
                                   "source": source, "kind": "liquidity_sweep"})
        if len({s["side"] for s in sweep_hits}) == 1:
            selected = sweep_hits[0]
            sweeps[selected["side"]] = selected
            events.append(selected.copy())
        for side, pivot in ((1, high_pivot), (-1, low_pivot)):
            if not pivot:
                continue
            previous_break = last_high_break if side == 1 else last_low_break
            crossed = (row["close"] > pivot["price"] >= rows[i - 1]["close"] if side == 1
                       else row["close"] < pivot["price"] <= rows[i - 1]["close"])
            if crossed and previous_break != pivot["origin_index"]:
                event = {"index": i, "time": row["time"], "side": side, "level": pivot["price"],
                         "kind": "CHOCH" if last_direction and last_direction != side else "BOS"}
                events.append(event)
                last_direction = side
                if side == 1:
                    last_high_break = pivot["origin_index"]
                else:
                    last_low_break = pivot["origin_index"]
                sweep = sweeps.get(side)
                if (sweep and 0 < i - sweep["index"] <= rules.sweep_to_break_bars
                        and (stamp(row["time"])-stamp(sweep["time"])).total_seconds() <= rules.sweep_to_break_bars*300):
                    breaks[side] = {"index": i, "time": row["time"], "sweep": sweep.copy(), "kind": event["kind"]}
        survivors = []
        for zone in active:
            side = zone["side"]
            if (i - zone["formed_index"] > rules.gap_max_age
                    or (stamp(row["time"])-stamp(zone["formed_at"])).total_seconds() > rules.gap_max_age*300):
                zone["status"] = "expired"
                continue
            invalid = row["close"] < zone["lower"] if side == 1 else row["close"] > zone["upper"]
            if invalid:
                zone["status"], zone["invalidated_at"] = "invalidated", row["time"]
                continue
            touch = row["low"] <= zone["upper"] and row["high"] >= zone["lower"]
            filled = row["low"] <= zone["lower"] if side == 1 else row["high"] >= zone["upper"]
            if touch and zone["status"] == "fresh":
                zone["first_touch_at"] = row["time"]
                zone["status"] = "filled" if filled else "touched"
                setup = breaks.get(side)
                if setup and setup["index"] <= i and setup["index"] - 2 <= zone["formed_index"] <= setup["index"] + rules.break_to_gap_bars and zone["formed_index"] >= setup["sweep"]["index"]:
                    key = (side, setup["sweep"]["index"])
                    low, high = (setup["sweep"]["extreme"], max(b["high"] for b in bars[setup["sweep"]["index"]:zone["formed_index"] + 1])) if side == 1 else (min(b["low"] for b in bars[setup["sweep"]["index"]:zone["formed_index"] + 1]), setup["sweep"]["extreme"])
                    mid = (zone["lower"] + zone["upper"]) / 2
                    retracement = (high - mid) / (high - low) if side == 1 and high > low else (mid - low) / (high - low) if high > low else -1
                    rejection = (row["close"] > zone["upper"] and row["close"] > row["open"]) if side == 1 else (row["close"] < zone["lower"] and row["close"] < row["open"])
                    at = stamp(row["time"]) + timedelta(minutes=5)
                    higher = context(at) if context else {}
                    reasons = []
                    if not rejection:
                        reasons.append("no_first_return_rejection")
                    if not rules.fib_min <= retracement <= rules.fib_max:
                        reasons.append("outside_swing_fib_band")
                    if key in consumed:
                        reasons.append("setup_already_used")
                    if context:
                        hour, quarter = higher.get("H1", {}), higher.get("M15", {})
                        if not hour.get("ema50") or side * (hour["ema20"] - hour["ema50"]) <= 0:
                            reasons.append("H1_bias_conflict")
                        if not quarter.get("ema20") or side * (quarter["close"] - quarter["ema20"]) <= 0:
                            reasons.append("M15_bias_conflict")
                    stop = setup["sweep"]["extreme"] - side * rules.structure_buffer_atr * atr
                    risk = side * (row["close"] - stop)
                    if not rules.min_risk_atr * atr <= risk <= rules.max_risk_atr * atr:
                        reasons.append("structural_stop_outside_ATR_bounds")
                    candidate = {"index": i, "signal_time": at.isoformat(), "bar_time": row["time"],
                                 "side": side, "reference_entry": row["close"], "structure_stop": stop,
                                 "atr": atr, "zone": {k: zone[k] for k in ("lower", "upper", "formed_index", "formed_at")},
                                 "sweep": setup["sweep"].copy(), "break_index": setup["index"],
                                 "break_time": setup["time"], "fib": {"low": low, "high": high,
                                 "retracement": retracement, "levels": fib_levels(low, high)},
                                 "rejection_reasons": reasons, "accepted": not reasons,
                                 "volatility_regime": row["volatility_regime"]}
                    candidates.append(candidate)
                    if not reasons:
                        signals.append(candidate.copy())
                        consumed.add(key)
            if filled:
                zone["status"] = "filled"
                continue
            survivors.append(zone)
        active = survivors
        continuous = (i >= 2 and (stamp(row["time"])-stamp(rows[i-1]["time"])).total_seconds() == 300
                      and (stamp(rows[i-1]["time"])-stamp(rows[i-2]["time"])).total_seconds() == 300)
        if continuous and rows[i - 1]["atr"]:
            side = 1 if row["low"] > rows[i - 2]["high"] else -1 if row["high"] < rows[i - 2]["low"] else 0
            if side:
                lower, upper = (rows[i - 2]["high"], row["low"]) if side == 1 else (row["high"], rows[i - 2]["low"])
                body = abs(rows[i - 1]["close"] - rows[i - 1]["open"])
                directional = side * (rows[i - 1]["close"] - rows[i - 1]["open"]) > 0
                if upper - lower >= rules.min_gap_atr * atr and body >= rules.displacement_body_atr * rows[i - 1]["atr"] and directional:
                    zone = {"side": side, "lower": lower, "upper": upper, "formed_index": i,
                            "formed_at": row["time"], "status": "fresh"}
                    active.append(zone)
                    zones.append(zone)
    latest = rows[-1] if rows else {}
    swing_fib = fib_levels(low_pivot["price"], high_pivot["price"]) if low_pivot and high_pivot and low_pivot["price"] < high_pivot["price"] else None
    return {"version": VERSION, "rules": asdict(rules), "signals": signals,
            "candidates": candidates, "events": events, "pivots": pivots, "gaps": zones,
            "latest": {"bar": latest, "swing_high": high_pivot, "swing_low": low_pivot,
                       "structure_direction": last_direction, "swing_fibonacci": swing_fib,
                       "active_gaps": [dict(z) for z in active], "recent_sweeps": list(sweeps.values()),
                       "eligible_breaks": list(breaks.values()),
                       "activity_profile": activity_profile(bars) if bars else None,
                       "real_orderflow": "unavailable_without_trade_side_and_real_volume_feed"}}
