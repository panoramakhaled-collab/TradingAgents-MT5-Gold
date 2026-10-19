"""Safe graph configuration for broker spot gold research."""

from tradingagents.default_config import build_default_config


def gold_config(bars_path: str, results_dir: str) -> dict:
    config = build_default_config()
    config.update(
        {
            "mt5_bars_path": bars_path,
            "results_dir": results_dir,
            "output_language": "Arabic",
            "llm_provider": "chatgpt",
            "quick_think_llm": "gpt-6-luna",
            "deep_think_llm": "gpt-6-sol",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "checkpoint_enabled": False,
            "disable_memory_settlement": True,
            "memory_log_path": f"{results_dir}/gold_research_memory.md",
            "data_vendors": {
                **config["data_vendors"],
                "core_stock_apis": "mt5_readonly",
                "technical_indicators": "mt5_readonly",
            },
        }
    )
    return config


SELECTED_ANALYSTS = ("market",)


def run_gold_research(trade_date: str, bars_path: str, results_dir: str):
    """Run upstream TradingAgents graph; requires a separately authenticated LLM.

    News is deliberately excluded until a dated, auditable event archive is
    available. Equity fundamentals and social feeds do not fit spot gold.
    This function does not call any MT5 trading operation.
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(SELECTED_ANALYSTS, config=gold_config(bars_path, results_dir))
    return graph.propagate("XAUUSD", trade_date, asset_type="gold")
