"""CCXT data source for cryptocurrency markets (-USD, -USDT, etc.).

CCXT is a unified Python library that provides access to 100+ crypto
exchanges (Binance, OKX, Bybit, etc.) through a single interface.  No API
key required for public endpoints (OHLCV, ticker).

This module implements all nine data-fetch interfaces for the TradingAgents
vendor routing system.  Fundamentally, non-OHLCV categories (fundamentals,
news, insider transactions) return a clear ``NOT_APPLICABLE`` message since
cryptocurrencies do not have traditional financial statements or corporate
filings.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .symbol_utils import NoMarketDataError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Suffixes that identify this module's market.
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")

# Default exchange.
_DEFAULT_EXCHANGE = "binance"

# Indicator descriptions (shared with akshare_source.py).
_INDICATOR_DESCRIPTIONS: dict[str, str] = {
    "close_50_sma": "50 SMA: A medium-term trend indicator.",
    "close_200_sma": "200 SMA: A long-term trend benchmark.",
    "close_10_ema": "10 EMA: A responsive short-term average.",
    "macd": "MACD: Computes momentum via differences of EMAs.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line.",
    "macdh": "MACD Histogram: Shows gap between MACD line and its signal.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions.",
    "boll": "Bollinger Middle: A 20 SMA basis for Bollinger Bands.",
    "boll_ub": "Bollinger Upper Band: Signals overbought conditions.",
    "boll_lb": "Bollinger Lower Band: Signals oversold conditions.",
    "atr": "ATR: Averages true range to measure volatility.",
    "vwma": "VWMA: A moving average weighted by volume.",
    "mfi": "MFI: Money Flow Index using price and volume.",
}

# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------


def _symbol_to_pair(symbol: str) -> str:
    """Convert a TradingAgents crypto symbol to an exchange pair.

    ``BTC-USD`` → ``BTC/USDT``
    ``ETH-USDT`` → ``ETH/USDT``
    ``SOL-BTC`` → ``SOL/BTC``
    """
    t = symbol.strip().upper()
    # Validate that it's actually a crypto symbol.
    if not t.endswith(_CRYPTO_SUFFIXES):
        raise NoMarketDataError(
            symbol, symbol,
            "not a crypto symbol; no crypto suffix found",
        )

    # Split on the last '-'.
    if "-" in t:
        base, quote = t.rsplit("-", 1)
        # Map USD → USDT for exchange trading pairs.
        if quote == "USD":
            quote = "USDT"
        return f"{base}/{quote}"
    raise NoMarketDataError(symbol, symbol, f"cannot parse pair from {t}")


def _is_crypto_symbol(symbol: str) -> bool:
    """Return ``True`` if ``symbol`` looks like a crypto ticker."""
    return symbol.strip().upper().endswith(_CRYPTO_SUFFIXES)


# ---------------------------------------------------------------------------
# OHLCV data fetching
# ---------------------------------------------------------------------------


def _fetch_ohlcv_df(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Fetch crypto OHLCV data via ccxt and return a normalized DataFrame.

    Returns columns: ``Date`` (datetime), ``Open``, ``High``, ``Low``,
    ``Close``, ``Volume``.
    """
    import ccxt

    pair = _symbol_to_pair(symbol)
    exchange_id = _DEFAULT_EXCHANGE

    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise NoMarketDataError(
            symbol, pair,
            f"unknown exchange {exchange_id!r}",
        )

    exchange = exchange_class({
        "enableRateLimit": True,
    })

    if not exchange.has.get("fetchOHLCV", False):
        raise NoMarketDataError(
            symbol, pair,
            f"{exchange_id} does not support fetchOHLCV",
        )

    # Convert dates to millisecond timestamps.
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    since = int(start_dt.timestamp() * 1000)
    until = int(end_dt.timestamp() * 1000)

    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe="1d", since=since, params={"until": until})
    except Exception as exc:
        raise NoMarketDataError(
            symbol, pair,
            f"ccxt fetch_ohlcv failed: {exc}",
        ) from exc

    if not ohlcv:
        raise NoMarketDataError(
            symbol, pair,
            f"no OHLCV data returned between {start_date} and {end_date}",
        )

    df = pd.DataFrame(ohlcv, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop(columns=["timestamp"])

    # Round prices.
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df = df.dropna(subset=["Close"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# core_stock_apis: get_stock_data
# ---------------------------------------------------------------------------


def get_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Crypto OHLCV data as a CSV string.

    Matches the output contract of ``y_finance.get_YFin_data_online``.
    """
    df = _fetch_ohlcv_df(symbol, start_date, end_date)

    df["Adj Close"] = df["Close"].round(2)
    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col].round(2)

    csv_string = df.to_csv(index=False)
    label = symbol.upper()
    header = (
        f"# Stock data for {label} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Source: ccxt ({_DEFAULT_EXCHANGE})\n\n"
    )
    return header + csv_string


# ---------------------------------------------------------------------------
# technical_indicators: get_stock_stats_indicators_window
# ---------------------------------------------------------------------------


def get_stock_stats_indicators_window(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30,
) -> str:
    """Compute a technical indicator for a crypto asset."""
    if indicator not in _INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator!r} is not supported. "
            f"Choose from: {list(_INDICATOR_DESCRIPTIONS.keys())}"
        )

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = end_dt - relativedelta(days=look_back_days)
    start_date = before_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    # Extra buffer for indicator calculation windows.
    buffer_start = (before_dt - relativedelta(days=400)).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_df(symbol, buffer_start, end_date)

    stock_df = wrap(df)
    stock_df["Date"] = stock_df["Date"].dt.strftime("%Y-%m-%d")
    stock_df[indicator]

    values: dict[str, str] = {}
    for _, row in stock_df.iterrows():
        val = row.get(indicator)
        if pd.isna(val):
            values[row["Date"]] = "N/A"
        else:
            values[row["Date"]] = f"{val:.2f}" if isinstance(val, float) else str(val)

    lines: list[str] = []
    current_dt = end_dt
    while current_dt >= before_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        val = values.get(date_str, "N/A: Not a trading day (weekend or holiday)")
        lines.append(f"{date_str}: {val}")
        current_dt -= relativedelta(days=1)

    return (
        f"## {indicator} values from {start_date} to {end_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _INDICATOR_DESCRIPTIONS.get(indicator, "")
    )


# ---------------------------------------------------------------------------
# fundamental_data — all NOT_APPLICABLE for crypto
# ---------------------------------------------------------------------------

_NOT_APPLICABLE_FMT = (
    "NOT_APPLICABLE: {purpose} is not applicable to cryptocurrency assets. "
    "Cryptocurrencies do not have traditional financial statements, "
    "corporate filings, or regulated insider transaction reporting. "
    "Please use on-chain data sources for crypto-specific analysis."
)


def get_fundamentals(ticker: str, curr_date: Optional[str] = None) -> str:
    """Not applicable for crypto."""
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return _NOT_APPLICABLE_FMT.format(purpose="Fundamental data")


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Not applicable for crypto."""
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return _NOT_APPLICABLE_FMT.format(purpose="Balance sheet")


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Not applicable for crypto."""
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return _NOT_APPLICABLE_FMT.format(purpose="Cash flow statement")


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Not applicable for crypto."""
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return _NOT_APPLICABLE_FMT.format(purpose="Income statement")


# ---------------------------------------------------------------------------
# news_data
# ---------------------------------------------------------------------------


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Crypto news (placeholder).

    TODO: Integrate a crypto-specific news source (e.g. CoinDesk API,
    CoinGecko news, or CryptoPanic). Currently returns a no-data message.
    """
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return (
        f"No news available for {ticker.upper()} from the crypto data source. "
        f"[TODO: crypto news source not yet integrated.]"
    )


def get_global_news(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Crypto global news (placeholder)."""
    _ = look_back_days, limit  # unused
    return (
        f"## Global Crypto News, around {curr_date}\n\n"
        f"[TODO: crypto macro news source not yet integrated.]"
    )


def get_insider_transactions(ticker: str) -> str:
    """Not applicable for crypto."""
    if not _is_crypto_symbol(ticker):
        raise NoMarketDataError(ticker, ticker, "not a crypto symbol")
    return _NOT_APPLICABLE_FMT.format(purpose="Insider transactions")
