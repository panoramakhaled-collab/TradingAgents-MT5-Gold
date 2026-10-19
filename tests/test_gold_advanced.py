import hashlib
import json
from datetime import datetime, timedelta

import pytest

from gold_mt5.advanced import Context, features, scan, validate_bars
from gold_mt5.compare import execute
from gold_mt5.live import describe


def bars(n=220):
    start = datetime(2026, 4, 20)
    return [
        {
            "time": (start + timedelta(minutes=5 * i)).isoformat(),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "tick_volume": 10,
        }
        for i in range(n)
    ]


def pattern():
    result = bars()
    for i, values in {
        190: (100, 103, 99, 100),
        193: (100, 101, 97, 99),
        200: (101, 104, 100, 101),
        201: (100.8, 101, 95.8, 96),
        202: (96, 97, 95, 96),
        203: (98.8, 99.5, 95.5, 96.5),
    }.items():
        result[i].update(dict(zip(("open", "high", "low", "close"), values, strict=True)))
    return result


def test_sweep_break_fvg_and_first_return_are_causal():
    data = pattern()
    prefix = scan(data[:204])
    whole = scan(data)
    assert prefix["signals"], "Synthetic sweep/BOS/FVG/rejection must actually trigger"
    assert prefix["signals"] == [s for s in whole["signals"] if s["index"] < 204]
    signal = prefix["signals"][0]
    assert signal["sweep"]["index"] < signal["break_index"] <= signal["index"]
    assert signal["zone"]["formed_index"] < signal["index"]
    assert all(p["known_index"] == p["origin_index"] + 2 for p in whole["pivots"])
    assert signal["side"] == -1


def test_context_never_reads_a_forming_higher_candle():
    data = bars(60)
    higher = [dict(data[0]), dict(data[12])]
    higher[1]["close"] = 101
    context = Context({"H1": higher})
    assert context.at(datetime(2026, 4, 20, 0, 59)) == {}
    assert context.at(datetime(2026, 4, 20, 1, 30))["H1"]["time"] == higher[0]["time"]


def test_same_bar_ambiguity_is_stop_first_and_entry_is_next_open():
    data = bars()
    data[205].update(open=101, high=110, low=90, close=102)
    signal = {"signal_time": data[205]["time"], "side": 1}
    trades = execute(data, [signal], datetime(2026, 4, 20), datetime(2026, 4, 21))
    assert len(trades) == 1
    assert trades[0]["entry"] == 101
    assert trades[0]["gross_r"] == pytest.approx(-1.0)
    assert trades[0]["target_hits"] == "[False, False, False]"


def test_no_entry_inside_a_closed_session_gap():
    data = bars()
    at = datetime.fromisoformat(data[205]["time"]) + timedelta(minutes=1)
    assert (
        execute(
            data,
            [{"signal_time": at.isoformat(), "side": 1}],
            datetime(2026, 4, 20),
            datetime(2026, 4, 21),
        )
        == []
    )


def test_export_rejects_duplicates_and_drops_forming_bar():
    data = bars(3)
    assert len(validate_bars(data, "M5", datetime(2026, 4, 20, 0, 12))) == 2
    with pytest.raises(ValueError, match="Duplicate"):
        validate_bars(data + [data[-1]], "M5")


def test_session_gap_cannot_create_an_intraday_fvg():
    data = pattern()
    for row in data[202:]:
        row["time"] = (datetime.fromisoformat(row["time"]) + timedelta(hours=2)).isoformat()
    model = scan(data)
    assert not any(g["formed_index"] == 202 for g in model["gaps"])


def test_tick_activity_is_never_labelled_real_volume():
    latest = features(bars())[-1]
    assert latest["tick_weighted_price_proxy"] is not None
    assert latest["trade_volume_vwap"] is None


def test_live_model_waits_when_no_full_pattern_exists_and_rejects_stale_quote():
    now = datetime(2026, 4, 20, 12)
    frames = {}
    for frame, minutes in (("M5", 5), ("M15", 15), ("H1", 60)):
        data = bars()
        for i, row in enumerate(data):
            row["time"] = (now - timedelta(minutes=(220 - i) * minutes)).isoformat()
        frames[frame] = data
    quote = {"update_time": now.isoformat(), "bid": 100.0, "ask": 100.1}
    assert describe(frames, quote, now)["plan"]["status"] == "WAIT"
    quote["update_time"] = (now - timedelta(seconds=31)).isoformat()
    with pytest.raises(ValueError, match="stale"):
        describe(frames, quote, now)


def test_partial_target_does_not_erase_the_remaining_stop_loss():
    data = bars()
    data[205].update(open=100, high=106.5, low=99, close=105)
    data[206].update(open=105, high=105, low=90, close=95)
    trade = execute(
        data,
        [{"signal_time": data[205]["time"], "side": 1}],
        datetime(2026, 4, 20),
        datetime(2026, 4, 21),
    )[0]
    assert trade["gross_r"] == pytest.approx(-1 / 3)


def test_server_clock_export_keeps_its_clock_and_point_in_time_cutoff(tmp_path):
    from gold_mt5.config import gold_config
    from tradingagents.dataflows.config import run_config
    from tradingagents.dataflows.vendors.mt5_readonly import load_frame

    data = {"M5": bars(3)}
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "gold-mt5-bars.json"
    path.write_text(json.dumps(data))
    metadata = {
        "symbol": "XAUUSD",
        "clock": "trade_server",
        "source": "read-only gateway",
        "retrieved_at_utc": "2026-04-21T00:00:00Z",
        "sha256_ohlc_dataset": hashlib.sha256(canonical).hexdigest(),
    }
    (tmp_path / "results.json").write_text(json.dumps({"metadata": metadata}))
    with run_config(gold_config(str(path), str(tmp_path))):
        frame = load_frame("XAUUSD", "M5", "2026-04-21")
        assert len(frame) == 3 and frame.Date.dt.tz is None
        with pytest.raises(ValueError, match="No completed"):
            load_frame("XAUUSD", "M5", "2026-04-20")
