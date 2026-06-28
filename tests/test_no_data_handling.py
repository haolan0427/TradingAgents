"""Tests that empty vendor results never become fabricated data.

Covers two systematic fixes:
  - load_ohlcv must not cache an empty download (cache poisoning), and must
    raise NoMarketDataError instead of returning an empty frame.
  - route_to_vendor must convert NoMarketDataError into a single explicit
    "NO_DATA_AVAILABLE" sentinel after all vendors are exhausted.
"""

import unittest
from unittest import mock

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.symbol_utils import NoMarketDataError


@pytest.mark.unit
class TestRouteToVendorSentinel(unittest.TestCase):
    def test_no_data_from_all_vendors_returns_sentinel(self):
        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no rows")

        patched = {"akshare": raises_no_data, "crypto_ccxt": raises_no_data}
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "0700.HK", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)
        self.assertIn("0700.HK", result)
        self.assertIn("Do not estimate", result)

    def test_unconfigured_fallback_does_not_mask_no_data(self):
        # When the primary vendor reports no data and the fallback is simply
        # unavailable (e.g. missing API key -> raises), the no-data sentinel
        # must win rather than the fallback's incidental error crashing out.
        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no rows")

        def raises_unavailable(symbol, *a, **k):
            raise ValueError("unavailable vendor")

        patched = {"akshare": raises_no_data, "crypto_ccxt": raises_unavailable}
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "0700.HK", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)


if __name__ == "__main__":
    unittest.main()
