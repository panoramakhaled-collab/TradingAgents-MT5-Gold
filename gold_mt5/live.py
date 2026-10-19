"""Describe a verified live demo setup using the very same historical rules."""
from __future__ import annotations

from datetime import datetime, timedelta

from gold_mt5.advanced import Context, Rules, features, scan, stamp, validate_bars


def _plan(side, entry, stop, expires, condition, proof):
    risk = side * (entry - stop)
    return {"status": "BUY" if side == 1 else "SELL", "entry": round(entry, 2),
            "stop": round(stop, 2), "tp1": round(entry + side*risk, 2),
            "tp2": round(entry + side*2*risk, 2), "tp3": round(entry + side*3*risk, 2),
            "expires_server": expires.isoformat(), "condition": condition,
            "proof": proof, "research_only": True}


def describe(frames: dict, quote: dict, server_now: datetime, rules: Rules = Rules()) -> dict:
    complete = {f: validate_bars(b, f, server_now) for f, b in frames.items()}
    if any(len(complete.get(f, [])) < 200 for f in ("M5", "M15", "H1")):
        raise ValueError("Not enough completed history for advanced model")
    if abs((server_now - stamp(quote["update_time"])).total_seconds()) > 30:
        raise ValueError("Current broker quote is stale")
    if not 0 < float(quote["bid"]) < float(quote["ask"]):
        raise ValueError("Invalid broker quote")
    for frame, minutes, max_age in (("M5",5,15),("M15",15,60),("H1",60,480)):
        age = server_now-(stamp(complete[frame][-1]["time"])+timedelta(minutes=minutes))
        if age.total_seconds() > max_age*60:
            raise ValueError(f"Stale completed {frame} history")
    context = Context(complete)
    model = scan(complete["M5"], rules, context.at)
    latest = model["latest"]
    last = latest["bar"]
    plan = {"status": "WAIT", "research_only": True,
            "condition": "No complete sweep -> structure break -> first FVG return with higher-frame alignment"}
    # A qualified signal is valid for the next M5 candle, with a bounded entry drift.
    if model["signals"]:
        signal = model["signals"][-1]
        age = (server_now - stamp(signal["signal_time"])).total_seconds()
        side = signal["side"]
        entry = float(quote["ask"] if side == 1 else quote["bid"])
        stop, atr = signal["structure_stop"], signal["atr"]
        risk = side * (entry-stop)
        if (0 <= age < 300 and signal["index"] == len(complete["M5"])-1
                and abs(entry-signal["reference_entry"]) <= .25*atr
                and rules.min_risk_atr*atr <= risk <= rules.max_risk_atr*atr):
            plan = _plan(side, entry, stop, stamp(signal["signal_time"])+timedelta(minutes=5),
                         "Qualified completed-M5 first-return rejection; entry must stay within 0.25 ATR of signal close", signal)
    pending = []
    higher = context.at(server_now)
    for zone in latest["active_gaps"]:
        if zone["status"] != "fresh":
            continue
        if (server_now-stamp(zone["formed_at"])).total_seconds() > rules.gap_max_age*300:
            continue
        for setup in latest["eligible_breaks"]:
            side = setup["sweep"]["side"]
            if (zone["side"] != side or not setup["index"]-2 <= zone["formed_index"] <= setup["index"]+rules.break_to_gap_bars
                    or zone["formed_index"] < setup["sweep"]["index"]):
                continue
            leg = complete["M5"][setup["sweep"]["index"]:zone["formed_index"]+1]
            low, high = (setup["sweep"]["extreme"], max(b["high"] for b in leg)) if side == 1 else (min(b["low"] for b in leg), setup["sweep"]["extreme"])
            mid = (zone["lower"]+zone["upper"])/2
            retrace = (high-mid)/(high-low) if side == 1 and high>low else (mid-low)/(high-low) if high>low else -1
            h, m = higher.get("H1", {}), higher.get("M15", {})
            if (not rules.fib_min <= retrace <= rules.fib_max or not h.get("ema50") or not m.get("ema20")
                    or side*(h["ema20"]-h["ema50"]) <= 0 or side*(m["close"]-m["ema20"]) <= 0):
                continue
            stop = setup["sweep"]["extreme"]-side*rules.structure_buffer_atr*last["atr"]
            risk = side*(mid-stop)
            if rules.min_risk_atr*last["atr"] <= risk <= rules.max_risk_atr*last["atr"]:
                p = _plan(side, mid, stop, server_now+timedelta(minutes=15),
                          "Wait for first touch and completed M5 rejection away from the gap; never enter on touch alone", setup)
                p.update(status="CONDITIONAL", side="BUY" if side==1 else "SELL",
                         entry_zone=[zone["lower"],zone["upper"]], fib_retracement=retrace)
                pending.append(p)
    if plan["status"] == "WAIT" and pending:
        plan = pending[-1]
    return {"version": model["version"], "as_of_server": server_now.isoformat(),
            "candle_clock": "MT5 trade server wall clock", "quote": quote,
            "plan": plan, "latest": latest, "recent_events": model["events"][-10:],
            "last_qualified_signal": model["signals"][-1:] or None,
            "recent_candidate_rejections": model["candidates"][-5:],
            "frames": {f: features(b)[-1] for f,b in complete.items() if b},
            "orderflow": {"real_delta": None, "reason": "No audited real trade-side feed connected",
                          "tick_activity_is_proxy": True}}
