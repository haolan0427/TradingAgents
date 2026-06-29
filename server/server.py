"""TradingAgents Web API — FastAPI application.

Usage:
    uvicorn server.server:app --host 0.0.0.0 --port 8000

Environment variables:
    REDIS_URL              Redis connection string (default: redis://localhost:6379/0)
    TRADINGAGENTS_*        All standard TradingAgents env vars are forwarded
                           to the worker via the config dict.
"""

from __future__ import annotations

import functools
import logging
import os
import redis as _redis
from fastapi import FastAPI, HTTPException
from rq import Queue

from server.schemas import (
    _DEFAULT_ANALYSTS,
    AnalystInfo,
    AnalyzeRequest,
    AnalyzeResponse,
    DebateContent,
    DecisionReport,
    DefaultsInfo,
    InfoResponse,
    ModelOption,
    ProgressInfo,
    ResearchDepthOption,
    ResultResponse,
    TickerValidateRequest,
    TickerValidateResponse,
    detect_asset_type,
    validate_ticker,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TradingAgents API",
    version="0.2.0",
    description=(
        "Multi-agent trading analysis backend. "
        "Submit a fully configured analysis task; poll for the result.\n\n"
        "**Step 1**: ``GET /api/info`` — discover available analysts, "
        "models, depth options.\n"
        "**Step 2**: ``POST /api/analyze`` — submit analysis with your "
        "preferred config.\n"
        "**Step 3**: ``GET /api/result/{task_id}`` — poll until done."
    ),
)

# ---------------------------------------------------------------------------
# Redis / RQ helpers
# ---------------------------------------------------------------------------


_LONG_TIMEOUT = 60 * 30  # 30 minutes — propagate() can take a while


@functools.lru_cache(maxsize=1)
def _queue() -> Queue:
    """Lazy RQ Queue singleton (one Redis connection, cached after first call)."""
    return Queue(
        "trading-tasks",
        connection=_redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=False,  # RQ needs bytes, not str
        ),
        default_timeout=_LONG_TIMEOUT,
    )


def _meta_from_task_id(task_id: str) -> dict | None:
    """Read ``job.meta`` from Redis, or return None if the job doesn't exist."""
    job = _queue().fetch_job(task_id)
    if job is None:
        return None
    return job.meta


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/api/info",
    response_model=InfoResponse,
    description="Discover available analysts, LLM models, research depths, and defaults.",
)
async def get_info():
    """Return all configurable options so clients can build their own UI."""
    analysts = [
        AnalystInfo(
            key="market",
            label="Market Analyst",
            supported_asset_types=["stock", "crypto"],
        ),
        AnalystInfo(
            key="social",
            label="Sentiment Analyst",
            supported_asset_types=["stock", "crypto"],
        ),
        AnalystInfo(
            key="news",
            label="News Analyst",
            supported_asset_types=["stock", "crypto"],
        ),
        AnalystInfo(
            key="fundamentals",
            label="Fundamentals Analyst",
            supported_asset_types=["stock"],  # excluded for crypto
        ),
    ]

    # Read defaults from env or DEFAULT_CONFIG.
    from tradingagents.default_config import DEFAULT_CONFIG

    depth_options = [
        ResearchDepthOption(
            label="Shallow",
            value=1,
            description=(
                "Quick research, few debate and strategy discussion rounds"
            ),
        ),
        ResearchDepthOption(
            label="Medium",
            value=3,
            description=(
                "Middle ground, moderate debate rounds and "
                "strategy discussion"
            ),
        ),
        ResearchDepthOption(
            label="Deep",
            value=5,
            description=(
                "Comprehensive research, in depth debate and "
                "strategy discussion"
            ),
        ),
    ]

    llm_models = {
        "quick": [
            ModelOption(
                label="DeepSeek V4 Flash - Latest V4 fast model",
                id="deepseek-v4-flash",
            ),
            ModelOption(
                label="DeepSeek V3.2",
                id="deepseek-chat",
            ),
        ],
        "deep": [
            ModelOption(
                label="DeepSeek V4 Pro - Latest V4 flagship model",
                id="deepseek-v4-pro",
            ),
            ModelOption(
                label="DeepSeek V3.2 (thinking)",
                id="deepseek-reasoner",
            ),
            ModelOption(
                label="DeepSeek V3.2",
                id="deepseek-chat",
            ),
        ],
    }

    defaults = DefaultsInfo(
        analysts=_DEFAULT_ANALYSTS,
        research_depth=DEFAULT_CONFIG.get("max_debate_rounds", 1),
        quick_think_llm=os.getenv(
            "TRADINGAGENTS_QUICK_THINK_LLM",
            DEFAULT_CONFIG.get("quick_think_llm", "deepseek-v4-flash"),
        ),
        deep_think_llm=os.getenv(
            "TRADINGAGENTS_DEEP_THINK_LLM",
            DEFAULT_CONFIG.get("deep_think_llm", "deepseek-v4-pro"),
        ),
    )

    return InfoResponse(
        supported_markets=[
            "Hong Kong stocks: .HK suffix (e.g. 0700.HK)",
            "China A-shares: .SS or .SZ suffix (e.g. 600519.SS, 000001.SZ)",
            "Cryptocurrencies: -USD suffix (e.g. BTC-USD)",
        ],
        supported_suffixes=[".HK", ".SS", ".SZ", "-USD"],
        analysts=analysts,
        research_depth_options=depth_options,
        llm_models=llm_models,
        defaults=defaults,
    )


@app.post(
    "/api/validate",
    response_model=TickerValidateResponse,
    description="Validate a ticker symbol without running any analysis.",
)
async def validate(body: TickerValidateRequest):
    """Check whether a ticker is supported and return its asset type."""
    try:
        normalised = validate_ticker(body.ticker)
        asset_type = detect_asset_type(normalised).value
        return TickerValidateResponse(
            valid=True,
            ticker=normalised,
            asset_type=asset_type,
            message=f"Valid {asset_type} ticker: {normalised}",
        )
    except ValueError as exc:
        return TickerValidateResponse(
            valid=False,
            ticker=body.ticker.strip().upper(),
            asset_type="unknown",
            message=str(exc),
        )


@app.post(
    "/api/analyze",
    status_code=202,
    response_model=AnalyzeResponse,
    responses={
        422: {"description": "Validation error (unsupported ticker or bad date format)"},
    },
)
async def analyze(body: AnalyzeRequest):
    """Enqueue a fully configured trading analysis task.

    Returns immediately with a ``task_id``.  Poll
    ``GET /api/result/{task_id}`` to retrieve the decision.

    All optional fields default to the values returned by
    ``GET /api/info`` when omitted.
    """
    # Resolve analysts: default to all, exclude fundamentals for crypto.
    analysts = body.analysts
    if analysts is not None:
        analyst_keys = [a.value for a in analysts]
    else:
        analyst_keys = None  # let the task resolve defaults

    # Map research_depth enum to string
    research_depth = body.research_depth.value if body.research_depth else None

    # Pass keyword arguments individually — RQ pops its own recognised
    # keys (meta, description, timeout, …) and forwards the rest as
    # keyword arguments to ``run_propagate``.
    job = _queue().enqueue(
        "server.tasks.run_propagate",
        body.ticker,
        body.date,
        analysts=analyst_keys,
        research_depth=research_depth,
        quick_think_llm=body.quick_think_llm,
        deep_think_llm=body.deep_think_llm,
        output_language=body.output_language,
        save_report=body.save_report or False,
        save_path=body.save_path,
        meta={
            "status": "pending",
            "progress": {"stage": "queued", "message": "Task queued"},
        },
        description=(
            f"TradingAgents analysis for {body.ticker} on {body.date}"
        ),
    )
    return AnalyzeResponse(task_id=job.id)


@app.get(
    "/api/result/{task_id}",
    response_model=ResultResponse,
    responses={
        404: {"description": "Task not found"},
    },
)
async def get_result(task_id: str):
    """Poll the status and result of a previously submitted analysis task."""
    meta = _meta_from_task_id(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    status = meta.get("status", "pending")
    progress_data = meta.get("progress", {})
    progress = ProgressInfo(
        stage=progress_data.get("stage", status),
        message=progress_data.get("message", ""),
    )
    result = meta.get("result")
    error = meta.get("error")

    if status == "failed":
        return ResultResponse(
            task_id=task_id,
            status="failed",
            progress=progress,
            decision=None,
            error=error,
        )

    if status != "done" or result is None:
        return ResultResponse(
            task_id=task_id,
            status=status,
            progress=progress,
            decision=None,
        )

    # Build the full decision response
    decision = DecisionReport(
        ticker=result.get("ticker", ""),
        date=result.get("date", ""),
        signal=result.get("signal", ""),
        market_report=result.get("market_report"),
        sentiment_report=result.get("sentiment_report"),
        news_report=result.get("news_report"),
        fundamentals_report=result.get("fundamentals_report"),
        investment_plan=result.get("investment_plan"),
        trader_proposal=result.get("trader_proposal"),
        debate=DebateContent(**result.get("debate", {})),
        final_decision=result.get("final_decision"),
    )
    return ResultResponse(
        task_id=task_id,
        status="done",
        progress=progress,
        decision=decision,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    """Simple health-check endpoint."""
    return {"status": "ok"}
