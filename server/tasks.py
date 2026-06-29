"""RQ background tasks for TradingAgents.

This module is imported by the RQ worker process (``rq worker trading-tasks``).
It must be picklable — no closures or lambdas in the top-level task function.

Each task wraps the synchronous ``TradingAgentsGraph.propagate()`` call and
stores progress + result on ``job.meta`` so the FastAPI endpoint can read them.
"""

from __future__ import annotations

import datetime
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from rq import get_current_job

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from server.schemas import (
    _DEFAULT_ANALYSTS,
    _RESEARCH_DEPTH_MAP,
    detect_asset_type,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _build_config(
    *,
    research_depth: str | None = None,
    quick_think_llm: str | None = None,
    deep_think_llm: str | None = None,
    output_language: str | None = None,
) -> dict:
    """Build a config dict from environment variables + API overrides.

    Environment variables take precedence over ``DEFAULT_CONFIG``;
    API-provided overrides take precedence over both.

    Sensitive settings (LLM provider, API keys, backend URLs) are read
    **only** from the environment — never from user-provided request data.
    """
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = os.getenv(
        "TRADINGAGENTS_LLM_PROVIDER", cfg.get("llm_provider", "deepseek")
    )
    cfg["deep_think_llm"] = (
        deep_think_llm
        or os.getenv("TRADINGAGENTS_DEEP_THINK_LLM")
        or cfg.get("deep_think_llm", "deepseek-v4-pro")
    )
    cfg["quick_think_llm"] = (
        quick_think_llm
        or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM")
        or cfg.get("quick_think_llm", "deepseek-v4-flash")
    )
    cfg["backend_url"] = os.getenv(
        "TRADINGAGENTS_LLM_BACKEND_URL", cfg.get("backend_url")
    )
    cfg["output_language"] = (
        output_language
        or os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE")
        or cfg.get("output_language", "Chinese")
    )

    # Research depth -> debate rounds
    if research_depth:
        rounds = _RESEARCH_DEPTH_MAP.get(research_depth)
        if rounds is not None:
            cfg["max_debate_rounds"] = rounds
            cfg["max_risk_discuss_rounds"] = rounds

    return cfg


# ---------------------------------------------------------------------------
# Report saving (mirrors cli/main.py::save_report_to_disk)
# ---------------------------------------------------------------------------


def _save_report_to_disk(
    final_state: dict,
    ticker: str,
    save_path: Path,
) -> Path:
    """Write the complete analysis report to disk with organised subfolders."""
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    for key, label, field in [
        ("market", "Market Analyst", "market_report"),
        ("social", "Sentiment Analyst", "sentiment_report"),
        ("news", "News Analyst", "news_report"),
        ("fundamentals", "Fundamentals Analyst", "fundamentals_report"),
    ]:
        content = final_state.get(field, "")
        if content:
            analysts_dir.mkdir(parents=True, exist_ok=True)
            (analysts_dir / f"{key}.md").write_text(content, encoding="utf-8")
            analyst_parts.append(f"### {label}\n{content}")

    if analyst_parts:
        sections.append(
            f"## I. Analyst Team Reports\n\n"
            + "\n\n".join(analyst_parts)
        )

    # 2. Research
    debate = final_state.get("investment_debate_state", {})
    if debate:
        research_dir = save_path / "2_research"
        research_parts = []
        for key, label, field in [
            ("bull", "Bull Researcher", "bull_history"),
            ("bear", "Bear Researcher", "bear_history"),
            ("manager", "Research Manager", "judge_decision"),
        ]:
            content = debate.get(field, "")
            if content:
                research_dir.mkdir(parents=True, exist_ok=True)
                (research_dir / f"{key}.md").write_text(content, encoding="utf-8")
                research_parts.append(f"### {label}\n{content}")

        if research_parts:
            sections.append(
                f"## II. Research Team Decision\n\n"
                + "\n\n".join(research_parts)
            )

    # 3. Trading
    trader_plan = final_state.get("trader_investment_plan", "")
    if trader_plan:
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(parents=True, exist_ok=True)
        (trading_dir / "trader.md").write_text(trader_plan, encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{trader_plan}")

    # 4. Risk Management
    risk = final_state.get("risk_debate_state", {})
    if risk:
        risk_dir = save_path / "4_risk"
        risk_parts = []
        for key, label, field in [
            ("aggressive", "Aggressive Analyst", "aggressive_history"),
            ("conservative", "Conservative Analyst", "conservative_history"),
            ("neutral", "Neutral Analyst", "neutral_history"),
        ]:
            content = risk.get(field, "")
            if content:
                risk_dir.mkdir(parents=True, exist_ok=True)
                (risk_dir / f"{key}.md").write_text(content, encoding="utf-8")
                risk_parts.append(f"### {label}\n{content}")

        if risk_parts:
            sections.append(
                f"## IV. Risk Management Team Decision\n\n"
                + "\n\n".join(risk_parts)
            )

        # 5. Portfolio Manager
        pm_decision = risk.get("judge_decision", "")
        if pm_decision:
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(parents=True, exist_ok=True)
            (portfolio_dir / "decision.md").write_text(pm_decision, encoding="utf-8")
            sections.append(
                f"## V. Portfolio Manager Decision\n\n"
                f"### Portfolio Manager\n{pm_decision}"
            )

    # Write consolidated report
    header = (
        f"# Trading Analysis Report: {ticker}\n\n"
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    report_path = save_path / "complete_report.md"
    report_path.write_text(header + "\n\n".join(sections), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Result builder (mirrors cli/main.py::display_complete_report struct)
# ---------------------------------------------------------------------------


def _build_result_dict(
    final_state: dict,
    signal: str,
    ticker: str,
    trade_date: str,
) -> dict:
    """Build the rich result dict stored in ``job.meta["result"]``.

    Structure mirrors the CLI's ``display_complete_report()`` output so
    API consumers get the same information.
    """
    debate_state = final_state.get("investment_debate_state", {})
    risk_state = final_state.get("risk_debate_state", {})

    return {
        "ticker": ticker,
        "date": trade_date,
        "signal": signal,
        # Analyst reports
        "market_report": final_state.get("market_report", ""),
        "sentiment_report": final_state.get("sentiment_report", ""),
        "news_report": final_state.get("news_report", ""),
        "fundamentals_report": final_state.get("fundamentals_report", ""),
        # Research team
        "investment_plan": final_state.get("investment_plan", ""),
        "trader_proposal": final_state.get("trader_investment_plan", ""),
        # Full final decision (Portfolio Manager)
        "final_decision": final_state.get("final_trade_decision", ""),
        # Debate content
        "debate": {
            "bull_vs_bear": debate_state.get("history", ""),
            "risk_discussion": risk_state.get("history", ""),
        },
    }


# ---------------------------------------------------------------------------
# Main task function
# ---------------------------------------------------------------------------


def run_propagate(
    ticker: str,
    trade_date: str,
    *,
    analysts: list[str] | None = None,
    research_depth: str | None = None,
    quick_think_llm: str | None = None,
    deep_think_llm: str | None = None,
    output_language: str | None = None,
    save_report: bool = False,
    save_path: str | None = None,
) -> None:
    """RQ job: run ``TradingAgentsGraph.propagate()`` and persist result.

    The current job is retrieved via ``rq.get_current_job()`` so the caller
    does not need to pass a job id — ``Queue.enqueue()`` handles that.

    Progress is written to ``job.meta`` at three points:
    - ``queued``  (set by the enqueuing side, or implicit)
    - ``running`` (before calling propagate)
    - ``done`` or ``failed``

    All ``*``-only kwargs come from the API request body.
    """
    job = get_current_job()
    if job is None:
        raise RuntimeError("run_propagate must be called from within an RQ worker")

    try:
        _set_meta(job, "running", "Starting analysis pipeline")

        # Resolve analysts
        asset_type_value = detect_asset_type(ticker).value
        selected_analysts = analysts or _DEFAULT_ANALYSTS
        # Auto-exclude fundamentals for crypto (matches CLI behaviour).
        if asset_type_value == "crypto" and "fundamentals" in selected_analysts:
            selected_analysts = [
                a for a in selected_analysts if a != "fundamentals"
            ]
            logger.info(
                "Excluded fundamentals analyst for crypto ticker %s", ticker
            )

        # Build config
        config = _build_config(
            research_depth=research_depth,
            quick_think_llm=quick_think_llm,
            deep_think_llm=deep_think_llm,
            output_language=output_language,
        )

        _set_meta(
            job, "running",
            f"Initialising graph with {len(selected_analysts)} analyst(s)"
            f" and depth={config.get('max_debate_rounds', 1)}"
        )

        # Instantiate graph with the full configuration.
        graph = TradingAgentsGraph(
            selected_analysts=selected_analysts,
            debug=False,
            config=config,
        )

        _set_meta(job, "running", "Executing propagation")

        final_state, signal = graph.propagate(
            ticker, trade_date, asset_type=asset_type_value
        )

        # Build result dict.
        result = _build_result_dict(final_state, signal, ticker, trade_date)

        # Save report to disk if requested.
        if save_report:
            try:
                if save_path:
                    report_dir = Path(save_path)
                else:
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    report_dir = (
                        Path.cwd() / "reports" / f"{ticker}_{timestamp}"
                    )
                report_file = _save_report_to_disk(
                    final_state, ticker, report_dir
                )
                result["_saved_report_path"] = str(report_file.resolve())
            except Exception as save_err:
                logger.warning("Failed to save report: %s", save_err)
                result["_saved_report_path"] = f"ERROR: {save_err}"

        _set_meta(job, "done", "Analysis complete", result=result)

    except Exception as exc:
        logger.exception("Propagation failed for %s on %s", ticker, trade_date)
        _set_meta(
            job, "failed", f"{type(exc).__name__}: {exc}",
            error=traceback.format_exc(),
        )


# ---------------------------------------------------------------------------
# Meta helper
# ---------------------------------------------------------------------------


def _set_meta(
    job,
    status: str,
    message: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Atomically update ``job.meta`` with status/progress/result."""
    meta: dict[str, Any] = {
        "status": status,
        "progress": {"stage": status, "message": message},
    }
    if result is not None:
        meta["result"] = result
    if error is not None:
        meta["error"] = error
    job.meta = meta
    job.save_meta()
