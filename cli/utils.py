import os
from pathlib import Path
from typing import List, Optional, Tuple

import questionary
from dotenv import find_dotenv, set_key
from rich.console import Console

from cli.models import AnalystType, AssetType
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import get_model_options

console = Console()

TICKER_INPUT_EXAMPLES = "0700.HK, 600519.SS, BTC-USD"

ANALYST_ORDER = [
    ("Market Analyst", AnalystType.MARKET),
    ("Sentiment Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
]

# Supported market suffixes (exchange suffixes and crypto pairs).
_SUPPORTED_EXCHANGE_SUFFIXES = {".HK", ".SS", ".SZ"}

# Known unsupported market suffixes — rejected with a clear error message.
_UNSUPPORTED_EXCHANGE_SUFFIXES = {
    ".NS", ".BO",     # India (NSE, BSE)
    ".T",              # Tokyo
    ".L",              # London
    ".TO",             # Toronto
    ".AX",             # Australia
}

CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def get_ticker() -> str:
    """Prompt the user to enter a ticker symbol, preserving exchange suffixes.

    Uses questionary.text (not typer.prompt, which strips trailing dot-suffixes
    like ``000404.SH`` on some shells) and validates the symbol charset so an
    obvious typo is caught before the run starts.
    """
    ticker = questionary.text(
        f"Enter ticker symbol (e.g. {TICKER_INPUT_EXAMPLES}):",
        validate=lambda x: (
            not x.strip()
            or (all(ch.isalnum() or ch in "._-^" for ch in x.strip()) and len(x.strip()) <= 32)
            or "Please enter a valid ticker symbol, e.g. AAPL, 000404.SZ, 0700.HK."
        ),
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if ticker is None:
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)

    ticker = normalize_ticker_symbol(ticker) if ticker.strip() else ""
    if not ticker:
        console.print("[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)
    validate_market_support(ticker)
    return ticker


def normalize_ticker_symbol(ticker: str) -> str:
    """Normalize ticker input while preserving exchange suffixes."""
    return ticker.strip().upper()


# ---- Market support validation ----

# Share-class suffixes (e.g. BRK.A, BRK.B) that look like exchange suffixes
# but are purely internal US notations — always allowed.
_SHARE_CLASS_SUFFIXES = frozenset({".A", ".B", ".C", ".U", ".WS"})


_SUPPORTED_MARKET_HELP = (
    "TradingAgents currently supports three markets:\n"
    "  - Hong Kong stocks: .HK suffix  (e.g. 0700.HK)\n"
    "  - China A-shares: .SS or .SZ suffix  (e.g. 600519.SS, 000001.SZ)\n"
    "  - Cryptocurrencies: -USD suffix (e.g. BTC-USD)"
)


def _extract_suffix(ticker: str) -> str | None:
    """Return the trailing exchange-style suffix of ticker, or None.

    A suffix is a dot followed by 1-3 uppercase letters at the end, e.g.
    ".HK", ".SS", ".T", ".L", ".B".  Hyphenated suffixes ("-USD") and
    caret-prefixed symbols ("^GSPC") are not exchange suffixes.
    """
    idx = ticker.rfind(".")
    if idx == -1:
        return None
    suffix = ticker[idx:]
    # Must be a dot followed by 1-3 uppercase letters
    rest = suffix[1:]
    if rest and rest.isalpha() and rest.isupper() and len(rest) <= 3:
        return suffix
    return None


def validate_market_support(ticker: str) -> None:
    """Raise ``ValueError`` if ``ticker`` belongs to a removed market.

    Accepts HK (.HK), China A-shares (.SS, .SZ), and crypto (-USD).
    Rejects US stocks (no suffix or share-class suffix) and all other
    removed exchanges with a helpful message listing supported markets.
    """
    # Normalise once.
    t = ticker.strip().upper()

    # Allow crypto pairs (hyphenated).
    if t.endswith(CRYPTO_SUFFIXES):
        return

    # Allow caret-prefixed index symbols used internally.
    if t.startswith("^"):
        return

    suffix = _extract_suffix(t)

    # No suffix means US stock — no longer supported.
    if suffix is None:
        raise ValueError(
            f"Ticker {t!r} is a US stock (no exchange suffix). "
            f"US stocks are not supported.\n"
            f"{_SUPPORTED_MARKET_HELP}"
        )

    # Allow explicitly supported exchange suffixes.
    if suffix in _SUPPORTED_EXCHANGE_SUFFIXES:
        return

    # Share-class suffixes (BRK.A, BRK.B) are a US convention — reject.
    if suffix in _SHARE_CLASS_SUFFIXES:
        raise ValueError(
            f"Ticker suffix {suffix!r} is a US share-class notation. "
            f"US stocks are not supported.\n"
            f"{_SUPPORTED_MARKET_HELP}"
        )

    # Reject known-removed exchanges with a specific message.
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

    # Any other unknown exchange suffix — reject with generic message.
    raise ValueError(
        f"Unrecognised ticker suffix {suffix!r}.\n"
        f"{_SUPPORTED_MARKET_HELP}"
    )


def detect_asset_type(ticker: str) -> AssetType:
    normalized_ticker = ticker.strip().upper()
    if normalized_ticker.endswith(CRYPTO_SUFFIXES):
        return AssetType.CRYPTO
    return AssetType.STOCK


def filter_analysts_for_asset_type(
    analysts: List[AnalystType], asset_type: AssetType
) -> List[AnalystType]:
    if asset_type != AssetType.CRYPTO:
        return analysts
    return [
        analyst
        for analyst in analysts
        if analyst != AnalystType.FUNDAMENTALS
    ]


def get_analysis_date() -> str:
    """Prompt the user to enter a date in YYYY-MM-DD format."""
    import re
    from datetime import datetime

    def validate_date(date_str: str) -> bool:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: validate_date(x.strip())
        or "Please enter a valid date in YYYY-MM-DD format.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    return date.strip()


def select_analysts(asset_type: AssetType = AssetType.STOCK) -> List[AnalystType]:
    """Select analysts using an interactive checkbox."""
    available_analysts = filter_analysts_for_asset_type(
        [value for _, value in ANALYST_ORDER],
        asset_type,
    )
    choices = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in ANALYST_ORDER
            if value in available_analysts
        ],
        instruction="\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return choices


def select_research_depth() -> int:
    """Select research depth using an interactive selection."""

    # Define research depth options with their corresponding values
    DEPTH_OPTIONS = [
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
    ]

    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)

    return choice


def _prompt_custom_model_id() -> str:
    """Prompt user to type a custom model ID."""
    return questionary.text(
        "Enter model ID:",
        validate=lambda x: len(x.strip()) > 0 or "Please enter a model ID.",
    ).ask().strip()


def _select_model(provider: str, mode: str) -> str:
    """Select a model for the given provider and mode (quick/deep)."""
    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in get_model_options(provider, mode)
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print(f"\n[red]No {mode} thinking llm engine selected. Exiting...[/red]")
        exit(1)

    if choice == "custom":
        return _prompt_custom_model_id()

    return choice


def select_shallow_thinking_agent(provider) -> str:
    """Select shallow thinking llm engine using an interactive selection."""
    return _select_model(provider, "quick")


def select_deep_thinking_agent(provider) -> str:
    """Select deep thinking llm engine using an interactive selection."""
    return _select_model(provider, "deep")

def provider_default_url(provider_key: str) -> str | None:
    """Return the default backend URL for a provider key, or None if unknown."""
    if provider_key.lower() == "deepseek":
        return "https://api.deepseek.com"
    return None


def select_llm_provider() -> tuple[str, str | None]:
    """Select the LLM provider and its API endpoint.

    Now only DeepSeek is supported.
    """
    provider = "deepseek"
    url = "https://api.deepseek.com"
    return provider, url


def ensure_api_key(provider: str) -> Optional[str]:
    """Make sure the API key for `provider` is available in the environment.

    If the env var is already set, returns its value untouched. Otherwise
    interactively prompts the user, persists the value to the project's
    .env file via python-dotenv's set_key (creating .env if needed), and
    exports it into os.environ so the current process picks it up.

    Returns None for providers that do not require a key (e.g. ollama)
    and for providers not found in the canonical mapping.
    """
    env_var = get_api_key_env(provider)
    if env_var is None:
        return None  # ollama / unknown — no key check possible

    existing = os.environ.get(env_var)
    if existing:
        return existing

    console.print(
        f"\n[yellow]{env_var} is not set in your environment.[/yellow]"
    )
    key = questionary.password(
        f"Paste your {env_var} (will be saved to .env):",
        style=questionary.Style([
            ("text", "fg:cyan"),
            ("highlighted", "noinherit"),
        ]),
    ).ask()
    if not key:
        console.print(
            f"[red]Skipped. API calls will fail until {env_var} is set.[/red]"
        )
        return None

    env_path = find_dotenv(usecwd=True) or str(Path.cwd() / ".env")
    Path(env_path).touch(exist_ok=True)
    set_key(env_path, env_var, key)
    os.environ[env_var] = key
    console.print(f"[green]Saved {env_var} to {env_path}[/green]")
    return key


