"""Write a dated local chart annotation, never an order or unattended task."""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


def write(snapshot: dict, terminal: Path):
    advanced = snapshot["advanced"]
    plan = advanced["plan"]
    files = terminal / "MQL5/Files"
    files.mkdir(parents=True, exist_ok=True)
    asof = datetime.fromisoformat(advanced["as_of_server"]).strftime("%Y.%m.%d %H:%M:%S")
    for frame in ("M5", "M15", "H1", "H4", "D1"):
        current = plan if frame == "M5" else {"status": "WAIT", "condition": "Context chart; entry model is on M5"}
        fields = {"status": current["status"], "as_of_server": asof,
                  "condition": current["condition"].replace(",", ";")}
        expiry = current.get("expires_server")
        if expiry:
            fields["expires_server"] = datetime.fromisoformat(expiry).strftime("%Y.%m.%d %H:%M:%S")
        for key in ("entry", "stop", "tp1", "tp2", "tp3"):
            if key in current:
                fields[key] = current[key]
        with (files / f"KhaledGoldAdvanced_plan_{frame}.csv").open("w", newline="", encoding="ascii") as f:
            csv.writer(f).writerows(fields.items())
    print("Dated chart model status written:", plan["status"], asof)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--terminal", type=Path, required=True)
    args = parser.parse_args()
    write(json.loads(args.snapshot.read_text()), args.terminal)
