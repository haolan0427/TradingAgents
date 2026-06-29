"""Pydantic models for the TradingAgents Web API.

No import from ``tradingagents`` here so that the schema definitions
stay independent and can be imported by API clients or documentation
generators without pulling in the full agent framework.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnalystKey(str, Enum):
    """Keys matching ``cli/models.py::AnalystType`` values."""

    MARKET = "market"
    SENTIMENT = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"


class ResearchDepth(str, Enum):
    """Named depth levels; values mapped to debate-round counts."""

    SHALLOW = "shallow"   # -> 1
    MEDIUM = "medium"     # -> 3
    DEEP = "deep"         # -> 5


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


# Supported market suffixes (mirrors cli/utils.py and trading_graph.py).
_SUPPORTED_EXCHANGE_SUFFIXES = {".HK", ".SS", ".SZ"}
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")
_SHARE_CLASS_SUFFIXES = frozenset({".A", ".B", ".C", ".U", ".WS"})
_UNSUPPORTED_EXCHANGE_SUFFIXES = {
    ".NS", ".BO", ".T", ".L", ".TO", ".AX",
}

_SUPPORTED_MARKET_HELP = (
    "TradingAgents currently supports:\n"
    "  - Hong Kong stocks: .HK suffix  (e.g. 0700.HK)\n"
    "  - China A-shares: .SS or .SZ suffix  (e.g. 600519.SS, 000001.SZ)\n"
    "  - Cryptocurrencies: -USD suffix (e.g. BTC-USD)"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_suffix(ticker: str) -> str | None:
    """Return trailing exchange-style suffix (e.g. ``.HK``) or ``None``."""
    idx = ticker.rfind(".")
    if idx == -1:
        return None
    suffix = ticker[idx:]
    rest = suffix[1:]
    if rest and rest.isalpha() and rest.isupper() and len(rest) <= 3:
        return suffix
    return None


def detect_asset_type(ticker: str) -> AssetType:
    """Detect asset type from ticker suffix (mirrors ``cli/utils.py``)."""
    normalized = ticker.strip().upper()
    if normalized.endswith(_CRYPTO_SUFFIXES):
        return AssetType.CRYPTO
    return AssetType.STOCK


def validate_ticker(ticker: str) -> str:
    """Validate and normalise a ticker; raise ``ValueError`` if unsupported.

    Mirrors ``cli/utils.validate_market_support()`` and
    ``trading_graph._validate_market_support()``.
    """
    t = ticker.strip().upper()

    if t.endswith(_CRYPTO_SUFFIXES):
        return t
    if t.startswith("^"):
        return t

    suffix = _extract_suffix(t)

    if suffix is None:
        raise ValueError(
            f"Ticker {t!r} is a US stock (no exchange suffix). "
            f"US stocks are not supported.\n{_SUPPORTED_MARKET_HELP}"
        )
    if suffix in _SUPPORTED_EXCHANGE_SUFFIXES:
        return t
    if suffix in _SHARE_CLASS_SUFFIXES:
        raise ValueError(
            f"Ticker suffix {suffix!r} is a US share-class notation. "
            f"US stocks are not supported.\n{_SUPPORTED_MARKET_HELP}"
        )
    if suffix in _UNSUPPORTED_EXCHANGE_SUFFIXES:
        _market_name = {
            ".NS": "India (NSE)", ".BO": "India (BSE)",
            ".T": "Tokyo", ".L": "London",
            ".TO": "Canada (Toronto)", ".AX": "Australia (ASX)",
        }.get(suffix, suffix)
        raise ValueError(
            f"Ticker suffix {suffix!r} ({_market_name}) is not supported.\n"
            f"{_SUPPORTED_MARKET_HELP}"
        )
    raise ValueError(
        f"Unrecognised ticker suffix {suffix!r}.\n{_SUPPORTED_MARKET_HELP}"
    )


_RESEARCH_DEPTH_MAP = {
    "shallow": 1,
    "medium": 3,
    "deep": 5,
}

_ANALYST_LABELS = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

_DEFAULT_ANALYSTS = ["market", "social", "news", "fundamentals"]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    """Full configuration for a trading analysis task."""

    ticker: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description=(
            "Ticker symbol with exchange suffix. "
            "Examples: 0700.HK, 600519.SS, BTC-USD."
        ),
    )
    date: str = Field(
        ...,
        description=(
            "Analysis date in YYYY-MM-DD format (e.g. 2024-05-10). "
            "Must not be in the future."
        ),
    )
    analysts: Optional[list[AnalystKey]] = Field(
        default=None,
        description=(
            "List of analysts to include. "
            "Valid keys: market, social, news, fundamentals. "
            "Defaults to all four. Fundamentals is auto-excluded for crypto."
        ),
    )
    research_depth: Optional[ResearchDepth] = Field(
        default=None,
        description=(
            "Research depth: shallow (1 round), medium (3 rounds), "
            "deep (5 rounds). Default: shallow."
        ),
    )
    quick_think_llm: Optional[str] = Field(
        default=None,
        description=(
            "Quick-thinking LLM model ID. "
            "Defaults to TRADINGAGENTS_QUICK_THINK_LLM env var "
            "or 'deepseek-v4-flash'."
        ),
    )
    deep_think_llm: Optional[str] = Field(
        default=None,
        description=(
            "Deep-thinking LLM model ID. "
            "Defaults to TRADINGAGENTS_DEEP_THINK_LLM env var "
            "or 'deepseek-v4-pro'."
        ),
    )
    output_language: Optional[str] = Field(
        default=None,
        description=(
            "Output language for reports. "
            "Currently only 'Chinese' is supported. Default: Chinese."
        ),
    )
    save_report: Optional[bool] = Field(
        default=False,
        description=(
            "Whether to save the full analysis report to disk on "
            "the server."
        ),
    )
    save_path: Optional[str] = Field(
        default=None,
        description=(
            "Custom directory path for saving the report. "
            "Only used when save_report is true. "
            "Defaults to ./reports/{TICKER}_{TIMESTAMP}/"
        ),
    )

    @field_validator("ticker")
    @classmethod
    def _validate_ticker(cls, v: str) -> str:
        return validate_ticker(v)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, v: str) -> str:
        if not _DATE_RE.match(v):
            raise ValueError(
                f"Date {v!r} does not match required format YYYY-MM-DD."
            )
        # Reject future dates (matches CLI behaviour).
        try:
            dt = datetime.strptime(v, "%Y-%m-%d").date()
            if dt > datetime.now().date():
                raise ValueError(f"Date {v!r} is in the future.")
        except ValueError as exc:
            if "future" in str(exc):
                raise
            raise ValueError(f"Invalid date {v!r}: {exc}")
        return v

    @field_validator("analysts")
    @classmethod
    def _validate_analysts(
        cls, v: list[AnalystKey] | None
    ) -> list[AnalystKey] | None:
        if v is not None and len(v) == 0:
            raise ValueError("At least one analyst must be selected.")
        return v


class TickerValidateRequest(BaseModel):
    """Request for validating a ticker symbol."""

    ticker: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Ticker symbol to validate (e.g. 0700.HK, BTC-USD).",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AnalyzeResponse(BaseModel):
    """Response returned immediately after enqueuing a task."""

    task_id: str = Field(..., description="UUID of the background task.")


class ProgressInfo(BaseModel):
    """Current execution stage."""

    stage: str = Field(default="queued", description="Stage key.")
    message: str = Field(default="", description="Human-readable progress message.")


class AnalystInfo(BaseModel):
    """Descriptor for an available analyst type."""

    key: str = Field(..., description="Programmatic key (e.g. 'market').")
    label: str = Field(..., description="Human-readable label.")
    supported_asset_types: list[str] = Field(
        ..., description="Asset types: stock, crypto."
    )


class ModelOption(BaseModel):
    """Descriptor for an LLM model option."""

    label: str = Field(..., description="Human-readable label.")
    id: str = Field(..., description="Model ID (programmatic).")


class ResearchDepthOption(BaseModel):
    """Descriptor for a research depth option."""

    label: str = Field(..., description="Human-readable label.")
    value: int = Field(..., description="Numeric value (debate rounds).")
    description: str = Field(..., description="Detailed description.")


class DefaultsInfo(BaseModel):
    """Default values for analysis parameters."""

    analysts: list[str] = Field(
        ..., description="Default analyst selection."
    )
    research_depth: int = Field(
        ..., description="Default research depth in rounds."
    )
    quick_think_llm: str = Field(
        ..., description="Default quick-thinking LLM model ID."
    )
    deep_think_llm: str = Field(
        ..., description="Default deep-thinking LLM model ID."
    )


class InfoResponse(BaseModel):
    """Available options for configuring an analysis."""

    supported_markets: list[str] = Field(
        ..., description="Human-readable supported market descriptions."
    )
    supported_suffixes: list[str] = Field(
        ..., description="Supported ticker suffixes."
    )
    analysts: list[AnalystInfo] = Field(
        ..., description="Available analyst types."
    )
    research_depth_options: list[ResearchDepthOption] = Field(
        ..., description="Available research depths."
    )
    llm_models: dict[str, list[ModelOption]] = Field(
        ..., description="Available LLM models by mode (quick/deep)."
    )
    defaults: DefaultsInfo = Field(
        ..., description="Default configuration values."
    )


class TickerValidateResponse(BaseModel):
    """Result of ticker validation."""

    valid: bool = Field(..., description="Whether the ticker is valid.")
    ticker: str = Field(..., description="Normalised ticker symbol.")
    asset_type: str = Field(
        ..., description="Detected asset type: stock or crypto."
    )
    message: str = Field(
        default="", description="Human-readable validation message."
    )


class DebateContent(BaseModel):
    """Full debate content from research and risk management teams."""

    bull_vs_bear: Optional[str] = Field(
        default=None,
        description=(
            "Investment debate history "
            "(Bull vs Bear researchers & Research Manager)."
        ),
    )
    risk_discussion: Optional[str] = Field(
        default=None,
        description=(
            "Risk management debate history "
            "(Aggressive/Neutral/Conservative analysts & PM)."
        ),
    )


class DecisionReport(BaseModel):
    """Full analysis result (present when status == 'done')."""

    ticker: str
    date: str
    signal: str = Field(
        ...,
        description=(
            "5-tier rating: Buy / Overweight / Hold / Underweight / Sell."
        ),
    )
    # Analyst reports
    market_report: Optional[str] = Field(default=None)
    sentiment_report: Optional[str] = Field(default=None)
    news_report: Optional[str] = Field(default=None)
    fundamentals_report: Optional[str] = Field(default=None)

    # Research team
    investment_plan: Optional[str] = Field(
        default=None,
        description=(
            "Research Manager's consolidated investment plan."
        ),
    )
    trader_proposal: Optional[str] = Field(
        default=None,
        description="Trader's tactical execution proposal.",
    )

    # Debate content
    debate: Optional[DebateContent] = Field(default=None)

    # Full final decision markdown
    final_decision: Optional[str] = Field(
        default=None,
        description=(
            "Portfolio Manager's final trade decision (full markdown)."
        ),
    )


class ResultResponse(BaseModel):
    """Response for ``GET /api/result/{task_id}``."""

    task_id: str
    status: str = Field(
        ...,
        description="One of: pending, running (queued), done, failed.",
    )
    progress: Optional[ProgressInfo] = None
    decision: Optional[DecisionReport] = None
    error: Optional[str] = Field(
        default=None,
        description=(
            "Error details (only present when status == 'failed')."
        ),
    )
