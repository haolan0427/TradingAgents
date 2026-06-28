"""Akshare data source for A-share (.SS/.SZ) and Hong Kong (.HK) markets.

Akshare is a local Python library that fetches Chinese financial data
directly from Chinese financial websites (East Money, Sina, etc.),
requiring no API keys and accessible from mainland China without VPN.

This module implements all nine data-fetch interfaces for the
TradingAgents vendor routing system (core_stock_apis, technical_indicators,
fundamental_data, news_data).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .symbol_utils import NoMarketDataError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

# Map exchange suffix to market type for routing.
_SUFFIX_MAP: dict[str, str] = {
    ".SS": "A",
    ".SZ": "A",
    ".HK": "HK",
}

# All crypto suffixes (copied from trading_graph.py to avoid circular import).
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """Parse a TradingAgents symbol into (market_type, code).

    Returns ``("A", "600519")`` for ``600519.SS``,
    ``("HK", "00700")`` for ``0700.HK``.

    Raises ``NoMarketDataError`` if the suffix is not an akshare market
    (so the router's fallback chain can try the next vendor).
    """
    t = symbol.strip().upper()
    # Check crypto first — not our market.
    if t.endswith(_CRYPTO_SUFFIXES):
        raise NoMarketDataError(symbol, symbol, "crypto symbol, not an akshare market")
    if t.startswith("^"):
        raise NoMarketDataError(symbol, symbol, "index symbol, not an akshare market")

    for suffix, market in _SUFFIX_MAP.items():
        if t.endswith(suffix):
            code = t[: -len(suffix)]
            # Code must be purely numeric.
            if code.isdigit():
                return market, code
            raise NoMarketDataError(
                symbol, symbol,
                f"non-numeric code {code!r} for suffix {suffix}",
            )

    raise NoMarketDataError(
        symbol, symbol,
        f"unsupported suffix in symbol {symbol!r} (akshare covers .SS/.SZ/.HK)",
    )


def _exchange_prefix(code: str, market: str) -> str:
    """Return the exchange-prefixed code akshare expects for some APIs.

    ``ak.stock_financial_abstract`` needs ``"SH600519"`` or ``"SZ000001"``
    while most other APIs just use the plain numeric code.
    """
    if market == "A":
        # Guess exchange from first digit: 6 → Shanghai, 0/3 → Shenzhen
        if code.startswith("6"):
            return f"SH{code}"
        return f"SZ{code}"
    # HK stocks don't need a prefix for akshare's HK APIs.
    return code


# ---------------------------------------------------------------------------
# OHLCV data fetching (shared by get_stock_data and indicators)
# ---------------------------------------------------------------------------

def _fetch_ohlcv_df(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Fetch OHLCV data via akshare and return a normalized DataFrame.

    Returns columns: ``Date`` (datetime), ``Open``, ``High``, ``Low``,
    ``Close``, ``Volume`` (int64).  Raises ``NoMarketDataError`` when no
    data is returned.
    """
    import akshare as ak

    market, code = _parse_symbol(symbol)
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")

    try:
        if market == "A":
            # Forward-adjusted prices (qfq) for consistency with yfinance's
            # auto_adjust=True behaviour.
            raw = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
            col_map = {
                "日期": "Date",
                "开盘": "Open",
                "收盘": "Close",
                "最高": "High",
                "最低": "Low",
                "成交量": "Volume",
            }
            # 成交量 in akshare A-share is in 手 (lots of 100 shares).
            volume_factor = 100
        else:  # HK
            raw = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust_flag=1,
            )
            col_map = {
                "日期": "Date",
                "开盘": "Open",
                "收盘": "Close",
                "最高": "High",
                "最低": "Low",
                "成交量": "Volume",
            }
            # HK stock volume is in shares.
            volume_factor = 1
    except Exception as exc:
        raise NoMarketDataError(
            symbol, symbol,
            f"akshare fetch failed: {exc}",
        ) from exc

    if raw is None or raw.empty:
        raise NoMarketDataError(
            symbol, symbol,
            f"no data returned by akshare between {start_date} and {end_date}",
        )

    # Keep only the mapped columns.
    available = {k: v for k, v in col_map.items() if k in raw.columns}
    df = raw[list(available.keys())].rename(columns=available)

    # Parse date.
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])

    # Normalise numeric columns.
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce") * volume_factor

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
    """OHLCV data for A-share / HK stocks as a CSV string.

    Matches the output contract of ``y_finance.get_YFin_data_online``
    (``#``-prefixed header lines, then CSV body with columns
    ``Date,Open,High,Low,Close,Adj Close,Volume``).
    """
    df = _fetch_ohlcv_df(symbol, start_date, end_date)

    # Add Adj Close (duplicate Close — akshare forward-adjusted data is
    # already adjustment-aware).
    df["Adj Close"] = df["Close"].round(2)

    # Round for cleaner display.
    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col].round(2)

    csv_string = df.to_csv(index=False)

    label = symbol.upper()
    header = (
        f"# Stock data for {label} from {start_date} to {end_date}\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Source: akshare\n\n"
    )
    return header + csv_string


# ---------------------------------------------------------------------------
# technical_indicators: get_stock_stats_indicators_window
# ---------------------------------------------------------------------------

# Indicator descriptions (copied from y_finance.py for consistency).
_INDICATOR_DESCRIPTIONS: dict[str, str] = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data."
    ),
    "mfi": (
        "MFI: The Money Flow Index uses price and volume to measure buying/selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions."
    ),
}


def get_stock_stats_indicators_window(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30,
) -> str:
    """Compute a technical indicator across a look-back window.

    Returns Markdown-formatted text matching the contract of
    ``y_finance.get_stock_stats_indicators_window``.
    """
    if indicator not in _INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator!r} is not supported. "
            f"Choose from: {list(_INDICATOR_DESCRIPTIONS.keys())}"
        )

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = end_dt - relativedelta(days=look_back_days)
    start_date = before_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    # Fetch OHLCV data with extra buffer for indicator calculation.
    # stockstats needs enough rows for its internal windows.
    buffer_start = (before_dt - relativedelta(days=400)).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_df(symbol, buffer_start, end_date)

    # Calculate indicator via stockstats.
    stock_df = wrap(df)
    stock_df["Date"] = stock_df["Date"].dt.strftime("%Y-%m-%d")
    stock_df[indicator]  # trigger stockstats calculation

    # Build date→value map.
    values: dict[str, str] = {}
    for _, row in stock_df.iterrows():
        val = row.get(indicator)
        if pd.isna(val):
            values[row["Date"]] = "N/A"
        else:
            values[row["Date"]] = f"{val:.2f}" if isinstance(val, float) else str(val)

    # Build output for the requested date range.
    lines: list[str] = []
    current_dt = end_dt
    while current_dt >= before_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        val = values.get(date_str, "N/A: Not a trading day (weekend or holiday)")
        lines.append(f"{date_str}: {val}")
        current_dt -= relativedelta(days=1)

    result = (
        f"## {indicator} values from {start_date} to {end_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _INDICATOR_DESCRIPTIONS.get(indicator, "")
    )
    return result


# ---------------------------------------------------------------------------
# fundamental_data
# ---------------------------------------------------------------------------

def _akshare_fundamentals_df(code: str, market: str) -> pd.DataFrame:
    """Fetch financial abstract from akshare.

    Returns a DataFrame with one row per period; columns are financial
    metrics (ROE, EPS, etc.) with Chinese names.
    """
    import akshare as ak

    prefixed = _exchange_prefix(code, market)
    try:
        return ak.stock_financial_abstract(symbol=prefixed)
    except Exception as exc:
        raise NoMarketDataError(
            code, code,
            f"akshare financial abstract failed: {exc}",
        ) from exc


def get_fundamentals(ticker: str, curr_date: Optional[str] = None) -> str:
    """Company fundamentals overview via akshare.

    Returns ``# Company Fundamentals for ...`` followed by ``Key: Value``
    lines, matching the yfinance output contract.
    """
    market, code = _parse_symbol(ticker)
    df = _akshare_fundamentals_df(code, market)

    if df.empty:
        raise NoMarketDataError(ticker, ticker, "no fundamental data returned")

    # The latest row contains the most recent period.
    latest = df.iloc[-1]
    lines: list[str] = []
    for col in df.columns:
        val = latest[col]
        if pd.notna(val):
            lines.append(f"{col}: {val}")

    header = (
        f"# Company Fundamentals for {ticker.upper()}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Source: akshare\n\n"
    )
    return header + "\n".join(lines)


def _financial_statement(
    ticker: str,
    freq: str,
    curr_date: Optional[str],
    statement_type: str,
) -> str:
    """Generic helper for balance sheet / cash flow / income statement."""
    import akshare as ak

    market, code = _parse_symbol(ticker)

    api_map = {
        "balance_sheet": ak.stock_balance_sheet_by_report_em,
        "cashflow": ak.stock_cash_flow_sheet_by_report_em,
        "income": ak.stock_profit_sheet_by_report_em,
    }
    func = api_map.get(statement_type)
    if func is None:
        raise ValueError(f"Unknown statement type: {statement_type}")

    try:
        df = func(symbol=code)
    except Exception as exc:
        raise NoMarketDataError(
            ticker, ticker,
            f"akshare {statement_type} failed: {exc}",
        ) from exc

    if df is None or df.empty:
        raise NoMarketDataError(ticker, ticker, f"no {statement_type} data")

    # Filter by date to prevent look-ahead bias.
    if curr_date:
        # Try to find a date-like column to filter on.
        date_cols = [c for c in df.columns if "日期" in c or "报告期" in c or "date" in c.lower()]
        if date_cols:
            col = date_cols[0]
            df[col] = pd.to_datetime(df[col], errors="coerce")
            df = df[df[col] <= pd.to_datetime(curr_date)]

    csv_string = df.to_csv(index=False)
    header = (
        f"# {statement_type.replace('_', ' ').title()} data for {ticker.upper()} ({freq})\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Source: akshare\n\n"
    )
    return header + csv_string


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Balance sheet via akshare."""
    return _financial_statement(ticker, freq, curr_date, "balance_sheet")


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Cash flow statement via akshare."""
    return _financial_statement(ticker, freq, curr_date, "cashflow")


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Income statement via akshare."""
    return _financial_statement(ticker, freq, curr_date, "income")


# ---------------------------------------------------------------------------
# news_data
# ---------------------------------------------------------------------------

def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """News headlines for a stock via akshare (East Money).

    Returns Markdown-formatted headlines matching the yfinance news
    contract. Returns an empty-result placeholder when no news is found
    (never raises).
    """
    import akshare as ak

    try:
        market, code = _parse_symbol(ticker)
    except NoMarketDataError:
        return f"No news available for {ticker} from akshare."

    try:
        # Use the plain numeric code for stock_news_em.
        df = ak.stock_news_em(symbol=code)
    except Exception as exc:
        logger.warning("akshare news fetch failed for %s: %s", ticker, exc)
        return f"<news unavailable for {ticker}: akshare fetch error>"

    if df is None or df.empty:
        return f"No news found for {ticker}"

    # Parse date range.
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # The news DataFrame has columns like: 日期, 时间, 新闻标题, 文章来源, etc.
    news_lines: list[str] = []
    date_col = "日期" if "日期" in df.columns else df.columns[0]

    for _, row in df.iterrows():
        try:
            pub_date = pd.to_datetime(row[date_col], errors="coerce")
            if pd.isna(pub_date):
                continue
            if not (start_dt <= pub_date <= end_dt + pd.Timedelta(days=1)):
                continue
        except Exception:
            continue

        title = str(row.get("新闻标题", row.get("title", "No title")))
        source = str(row.get("文章来源", row.get("source", "East Money")))
        news_lines.append(
            f"### {title} (source: {source})\n"
            f"({pub_date.strftime('%Y-%m-%d')})\n"
        )

    if not news_lines:
        return f"No news found for {ticker} between {start_date} and {end_date}"

    return (
        f"## {ticker.upper()} News, from {start_date} to {end_date}:\n\n"
        + "\n".join(news_lines)
    )


def get_global_news(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Global / macro news via akshare.

    TODO: Replace with a proper Chinese macro news source when available.
    Currently returns a placeholder so the interface contract is preserved.
    """
    # akshare's stock_info_global can fetch some macro news, but the
    # integration is incomplete. Return a TODO placeholder for now.
    _ = look_back_days, limit  # unused placeholder
    return (
        f"## Global Market News, around {curr_date}\n\n"
        f"[TODO: Chinese macro news source not yet integrated. "
        f"akshare provides ak.stock_info_global(name=...) and "
        f"ak.news_eastmoney() as potential sources.]"
    )


def get_insider_transactions(ticker: str) -> str:
    """Insider transactions via akshare (Xueqiu source).

    Returns CSV-formatted data or a no-data message.
    """
    import akshare as ak

    try:
        market, code = _parse_symbol(ticker)
    except NoMarketDataError:
        return f"No insider transactions available for {ticker} from akshare."

    prefixed = _exchange_prefix(code, market)
    try:
        df = ak.stock_inner_trade_xq(symbol=prefixed)
    except Exception as exc:
        logger.warning("akshare insider trades failed for %s: %s", ticker, exc)
        # Not all symbols have insider data; return a clean no-data message.
        return (
            f"No insider transactions data available for {ticker} "
            f"via akshare (Xueqiu source)."
        )

    if df is None or df.empty:
        return f"No insider transactions reported for {ticker}"

    csv_string = df.to_csv(index=False)
    header = (
        f"# Insider Transactions data for {ticker.upper()}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Source: akshare (Xueqiu)\n\n"
    )
    return header + csv_string
