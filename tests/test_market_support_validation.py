"""Tests for market-support validation in CLI and programmatic paths.

Verifies that supported tickers (US, HK, China A-shares, crypto) pass
validation and unsupported tickers (Tokyo, London, India, Canada, Australia)
are rejected with clear error messages.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from cli.utils import validate_market_support, get_ticker
from tradingagents.graph.trading_graph import _validate_market_support as graph_validate


# ---- Shared helpers --------------------------------------------------------

_SUPPORTED = [
    # Hong Kong
    "0700.HK", "0005.HK", "9988.HK",
    # China A-shares Shanghai
    "600519.SS", "601318.SS", "600036.SS",
    # China A-shares Shenzhen
    "000001.SZ", "000002.SZ", "300750.SZ",
    # Crypto
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
    "BTC-USDT", "ETH-USDC",
    # Index symbols (internal use)
    "^HSI", "^GSPC", "^DJI",
]

_UNSUPPORTED = [
    # US stocks (no suffix)
    "AAPL", "SPY", "MSFT", "GOOGL",
    # US share-class suffixes
    "BRK.B", "BRK-A", "BF.B", "BRK.A", "BRK.C", "AAPL.U",
    # India
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS",
    ".BO", "RELIANCE.BO",
    # Tokyo
    "7203.T", "9984.T", "6758.T",
    # London
    "AZN.L", "HSBA.L", "BP.L",
    # Canada / Toronto
    "CNQ.TO", "RY.TO", "TD.TO",
    # Australia
    "BHP.AX", "CBA.AX", "WBC.AX",
    # Other unrecognised exchange suffixes
    "SAP.DE", "MC.PA",
]


# ---- CLI path tests --------------------------------------------------------

@pytest.mark.unit
class TestCliValidateMarketSupport(unittest.TestCase):
    """Tests for validate_market_support() as called from the CLI."""

    def test_allows_hong_kong(self):
        for ticker in ("0700.HK", "0005.HK", "9988.HK"):
            validate_market_support(ticker)

    def test_allows_china_a_shares(self):
        for ticker in ("600519.SS", "000001.SZ", "300750.SZ"):
            validate_market_support(ticker)

    def test_allows_crypto(self):
        for ticker in ("BTC-USD", "ETH-USD", "SOL-USDT"):
            validate_market_support(ticker)

    def test_allows_index_symbols(self):
        for ticker in ("^HSI", "^GSPC", "^DJI"):
            validate_market_support(ticker)

    def test_allows_case_insensitive(self):
        for ticker in ("0700.hk", "600519.ss", "000001.sz", "btc-usd"):
            validate_market_support(ticker)

    def test_rejects_india_nse(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("RELIANCE.NS")
        msg = str(ctx.exception)
        self.assertIn("India", msg)
        self.assertIn(".NS", msg)

    def test_rejects_india_bse(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("RELIANCE.BO")
        msg = str(ctx.exception)
        self.assertIn("India", msg)

    def test_rejects_tokyo(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("7203.T")
        msg = str(ctx.exception)
        self.assertIn("Tokyo", msg)
        self.assertIn(".T", msg)

    def test_rejects_london(self):
        with self.assertRaises(ValueError):
            validate_market_support("AZN.L")

    def test_rejects_toronto(self):
        with self.assertRaises(ValueError):
            validate_market_support("CNQ.TO")

    def test_rejects_australia(self):
        with self.assertRaises(ValueError):
            validate_market_support("BHP.AX")

    def test_rejects_us_stock_no_suffix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("AAPL")
        msg = str(ctx.exception)
        self.assertIn("US stock", msg)
        self.assertIn("not supported", msg.lower())

    def test_rejects_us_share_class_suffix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("BRK.B")
        msg = str(ctx.exception)
        self.assertIn("share-class", msg.lower())

    def test_rejects_unrecognised_suffix(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("SAP.DE")
        msg = str(ctx.exception)
        self.assertIn("Unrecognised", msg)
        self.assertIn("supported", msg.lower())

    def test_error_message_lists_supported_markets(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("AAPL")
        msg = str(ctx.exception)
        self.assertNotIn("US stocks", msg)
        self.assertIn("Hong Kong", msg)
        self.assertIn("China A-shares", msg)
        self.assertIn("Cryptocurrencies", msg)

    def test_error_message_contains_suffix_examples(self):
        with self.assertRaises(ValueError) as ctx:
            validate_market_support("7203.T")
        msg = str(ctx.exception)
        self.assertIn(".HK", msg)
        self.assertIn(".SS", msg)
        self.assertIn(".SZ", msg)


# ---- Programmatic (graph) path tests ---------------------------------------

@pytest.mark.unit
class TestGraphValidateMarketSupport(unittest.TestCase):
    """``_validate_market_support`` follows the same rules as the CLI version."""

    def test_allows_supported(self):
        for ticker in _SUPPORTED:
            graph_validate(ticker)  # must not raise

    def test_rejects_unsupported(self):
        for ticker in _UNSUPPORTED:
            with self.subTest(ticker=ticker):
                with self.assertRaises(ValueError):
                    graph_validate(ticker)

    def test_message_contains_help(self):
        with self.assertRaises(ValueError) as ctx:
            graph_validate("AZN.L")
        self.assertIn("supports", str(ctx.exception).lower())
        self.assertIn(".HK", str(ctx.exception))

    def test_rejects_share_class_suffixes(self):
        # Share-class suffixes are a US convention and should be rejected.
        for ticker in ("BRK.A", "BRK.B", "BRK.C", "AAPL.U", "AAPL.WS"):
            with self.assertRaises(ValueError):
                graph_validate(ticker)

    def test_lowercase_input(self):
        graph_validate("0700.hk")
        graph_validate("btc-usd")
        with self.assertRaises(ValueError):
            graph_validate("AApl")


# ---- Integration: get_ticker delegates to validate_market_support ----------

@pytest.mark.unit
class TestGetTickerValidation(unittest.TestCase):
    """``get_ticker()`` calls ``validate_market_support`` after normalisation."""

    @patch("cli.utils.questionary.text")
    def test_valid_ticker_accepted(self, mock_text):
        mock_text.return_value.ask.return_value = "0700.HK"
        result = get_ticker()
        self.assertEqual(result, "0700.HK")

    @patch("cli.utils.questionary.text")
    def test_crypto_ticker_accepted(self, mock_text):
        mock_text.return_value.ask.return_value = "BTC-USD"
        result = get_ticker()
        self.assertEqual(result, "BTC-USD")

    @patch("cli.utils.questionary.text")
    def test_hk_ticker_accepted(self, mock_text):
        mock_text.return_value.ask.return_value = "0700.HK"
        result = get_ticker()
        self.assertEqual(result, "0700.HK")

    @patch("cli.utils.questionary.text")
    def test_us_ticker_raises(self, mock_text):
        mock_text.return_value.ask.return_value = "AAPL"
        with self.assertRaises(ValueError):
            get_ticker()

    @patch("cli.utils.questionary.text")
    def test_removed_exchange_raises(self, mock_text):
        mock_text.return_value.ask.return_value = "7203.T"
        with self.assertRaises(ValueError):
            get_ticker()


if __name__ == "__main__":
    unittest.main()
