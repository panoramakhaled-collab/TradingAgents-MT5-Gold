"""Run the full TradingAgents research graph on verified, completed MT5 bars."""

import argparse
import json
from pathlib import Path

from gold_mt5.config import run_gold_research


def main():
    parser = argparse.ArgumentParser(description="Read-only XAUUSD TradingAgents research")
    parser.add_argument(
        "--bars", required=True, help="Verified MT5 JSON export beside results.json"
    )
    parser.add_argument("--date", required=True, help="Historical analysis date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Local folder for research output")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state, rating = run_gold_research(args.date, args.bars, str(out))
    report = {
        "date": args.date,
        "symbol": "XAUUSD",
        "rating": rating,
        "market_report": state.get("market_report"),
        "final_trade_decision": state.get("final_trade_decision"),
        "note": "Read-only research. No order execution or live price claim.",
    }
    (out / "research.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"Research saved in {out / 'research.json'}")


if __name__ == "__main__":
    main()
