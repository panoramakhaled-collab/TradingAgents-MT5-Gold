# TradingAgents for read-only MT5 XAUUSD research

This fork adds a broker spot-gold data adapter to the upstream TradingAgents
research graph. It accepts a **verified export** from the separate read-only
MT5 gateway: `gold-mt5-bars.json` plus its `results.json` provenance manifest.
The manifest must say XAUUSD, identify the read-only gateway, and contain a
matching SHA-256 hash. The exported bars and account details stay local.

## What was changed

- `mt5_readonly` is an explicit price and indicator vendor. It rejects other
  instruments, missing manifests, tampered exports, duplicate/unsorted candles,
  and unfinished historical candles. There is no fallback to Yahoo's `GC=F`.
- The verification snapshot shows D1, H4, H1 and M15 completed bars with
  EMA 20/50/200, RSI, MACD, ATR and Bollinger values.
- Gold research labels the instrument as broker spot XAUUSD and skips Yahoo
  company identity, equity fundamentals, social feeds and automatic memory
  settlement. News is excluded until an auditable point-in-time archive exists.
- The graph uses TradingAgents' market analyst, bull/bear debate, trader and
  risk review. It has **no MT5 trading or order APIs**.
- `gold_mt5.backtest` is a separate, deterministic execution proxy with a fixed
  H4/H1/M15 rule and explicit transaction cost assumptions. It is **not** a
  backtest of the LLM graph's decisions.

## Run

Install Python 3.11+ and this fork with `pip install -e .` inside a virtual
environment. Keep the MT5 export outside the Git repository.

```sh
python -m gold_mt5.backtest \
  --bars /path/to/gold-mt5-bars.json \
  --start 2026-04-20 --end 2026-10-19 \
  --out /path/to/local-results
```

The full TradingAgents graph needs a supported LLM account. For the selected
ChatGPT subscription provider, run `tradingagents login chatgpt` and complete
the login in the browser. Never paste an access token into chat or this repo.

```sh
python -m gold_mt5.run_model \
  --bars /path/to/gold-mt5-bars.json \
  --date 2026-10-19 \
  --out /path/to/local-research
```

Historical graph reports use only candles completed by the requested date.
They do not verify a live quote, live account connection, economic calendar,
or executable fill. The original upstream decision-rating backtest is not a
position or cash-ledger simulator, so it is not used to claim scalp returns.

## Data and execution limits

The cached MT5 export uses candle OHLC and recorded spreads, not historical
ticks. Spread cost is approximated as half the entry spread plus half the exit
spread, plus $0.10/oz round-trip slippage. Commission and swaps were unavailable
and are excluded. If stop and target fall within one candle, the stop wins.
The test reports risk multiples (`R`), not money P&L or an account return.

This project performs analysis only. It cannot open, edit or close a position.
