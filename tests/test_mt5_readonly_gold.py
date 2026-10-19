import hashlib
import json

import pytest

from gold_mt5.config import gold_config
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.router import get_vendor
from tradingagents.dataflows.vendors.mt5_readonly import load_frame


def _export(tmp_path):
    bars = {"D1": [
        {"time": day + "T00:00:00+00:00", "open": 100, "high": 102,
         "low": 99, "close": 101, "spread": 8}
        for day in ("2026-10-18", "2026-10-19")
    ]}
    path = tmp_path / "gold-mt5-bars.json"
    path.write_text(json.dumps(bars), encoding="utf-8")
    digest = hashlib.sha256(json.dumps(bars, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    (tmp_path / "results.json").write_text(json.dumps({"metadata": {
        "source": "selected demo account via read-only gateway", "symbol": "XAUUSD",
        "retrieved_at_utc": "2026-10-20T00:00:00+00:00", "sha256_ohlc_dataset": digest}}),
        encoding="utf-8")
    return path


def test_historical_day_does_not_see_unfinished_daily_candle(tmp_path):
    path = _export(tmp_path)
    with run_config(gold_config(str(path), str(tmp_path))):
        data = load_frame("XAUUSD", "D1", "2026-10-19")
        assert len(data) == 1
        assert data.iloc[-1].Date.strftime("%Y-%m-%d") == "2026-10-18"
        assert get_vendor("core_stock_apis", "get_stock_data") == "mt5_readonly"
        with pytest.raises(ValueError, match="exact XAUUSD"):
            load_frame("GC=F", "D1", "2026-10-19")


def test_export_tampering_fails_closed(tmp_path):
    path = _export(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"close": 101', '"close": 999'), encoding="utf-8")
    with run_config(gold_config(str(path), str(tmp_path))):
        with pytest.raises(ValueError, match="hash"):
            load_frame("XAUUSD", "D1", "2026-10-19")
