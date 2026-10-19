# Gold structure research v2

This extends the existing TradingAgents MT5 fork. It does not authenticate an
LLM account, run the LLM graph, or expose an order/execution API. The user's
existing choice to keep the deterministic model is preserved.

## Fixed entry model

1. A swing requires two bars on each side. Its price is unknown until the two
   right-hand bars close; historical markers identify the confirmation time.
2. Price sweeps a confirmed swing or previous server-day high/low and closes
   back inside. An opposite structure break must follow within 12 M5 bars and
   60 clock minutes. BOS/CHOCH describe observed structure, not institutional intent.
3. A three-candle FVG must have a middle-candle body at least 0.8 ATR and a gap
   at least 0.05 ATR. Bars separated by a session closure cannot create an
   intraday FVG. Gaps expire after 36 M5 bars or 180 clock minutes.
4. Only the first return can qualify. The completed M5 candle must reject
   away from the gap. The frozen sweep/displacement leg must place the gap
   midpoint within 38.2–78.6% retracement.
5. H1 EMA20/50 direction and M15 close relative to EMA20 must agree. Only
   higher-frame candles closed by the M5 decision are read.
6. The structural stop sits 0.15 ATR beyond the sweep extreme, with a risk
   distance between 0.7 and 3.5 ATR. The live descriptive plan uses 1R/2R/3R
   targets. No lot size is invented.

`advanced.py` is the shared causal engine for backtest, live description and
facts supplied to the TradingAgents market analyst. `live.py` returns WAIT
unless the whole chain qualifies; a fresh gap alone is not a signal. A
qualified live signal expires after its next M5 bar, with a bounded entry
drift of 0.25 ATR. Conditional plans require a future completed-candle rejection.

## Chart overlay

`gold_mt5/mt5/Khaled_Gold_Advanced.mq5` is an analysis-only MT5 indicator. It
draws confirmed pivots, BOS/CHOCH, liquidity sweeps, fresh/touched FVGs, prior-day
levels and confirmed-swing Fibonacci. The displayed context swing Fib is
distinct from the frozen setup leg used by the entry engine. The overlay
uses completed bars; it contains no trading, DLL, network or shell functions.

Orange tick-weighted price and tick-activity POC/value-area levels are labelled
**proxies**: each bar's typical price is weighted by its tick count. They do
not reconstruct intrabar trades, global gold volume, footprint or real delta.
Actual trade-volume VWAP is empty unless nonzero real volume is supplied.
Real order flow still requires an audited trade-side feed; it is not faked.

`deploy_charts.py` is an explicitly invoked setup script. Its operation allowlist
contains chart opening, indicator add/remove, template backup and indicator
inspection only. Every action verifies the selected account. It preserves
the seven standard indicators and replaces the old Fib/FVG overlay only after
the advanced indicator calculates. Old templates and the prior compiled file
are backed up. This chart-writing script is not advertised by the public MCP.

`write_overlay.py` writes a dated local annotation CSV. The indicator displays
its verification time and expires plans. No unattended updater or alerts are
started by these scripts.

## Reproduce with the user's separately configured read-only gateway

Use a Python environment with `mcp` and `httpx` (or the existing gateway env).
The gateway keeps credentials and account identity outside this repository.

```sh
python -m gold_mt5.export --gateway /path/to/mt5-readonly-mcp \
  --out results/advanced/data
python -m gold_mt5.compare --bars results/advanced/data/gold-mt5-bars.json \
  --out results/advanced/comparison --start 2026-04-20 --end 2026-10-19
python -m gold_mt5.report results/advanced/comparison
python -m gold_mt5.capture --gateway /path/to/mt5-readonly-mcp \
  --out results/advanced/live/latest.json --terminal /path/to/MT5
```

The export records naive server wall-clock timestamps rather than relabelling
them UTC. The hash and manifest are mandatory. Historical UTC offsets are not
assumed; session reports are grouped by server hour. Broker bars/results stay
under ignored `results/` and are not published to the fork.

The existing historical graph's date-only API is conservatively defined at
00:00 in its declared clock. The requested day's unfinished candles cannot
leak into a historical decision. Current analysis uses the separately verified
live gateway instead.

## Evaluation boundaries

The principal matched comparison holds M5 execution, 1.5 ATR stop, 1R/2R/3R
targets (equal thirds), two-hour horizon and historical cost assumptions
constant. Only entry rules differ. A separate predeclared arm uses structural
stops. The legacy M15 single-target/eight-hour model is also reported, with
its different exit policy clearly labelled.

Entries fill at the next available bar only when its timestamp exactly
matches signal close. Closed-session gaps cannot create imaginary fills.
When stop and target both lie within one candle, the stop is processed first.
Partial target fills do not erase the original stop on the remaining fraction.
Bar MAE/MFE are bounds, not reconstructed tick sequences. No break-even stop
is silently applied.

Reports include a later chronological window, monthly and regime summaries,
and a seeded bootstrap diagnostic of expectancy. This is a small retrospective
sample, not an independent future test; resampling ignores serial dependence.
There is no parameter search for a flattering six-month result. More features
and a successful installation are not evidence of profitability. Consult the
local comparison report before using any demo setup.
