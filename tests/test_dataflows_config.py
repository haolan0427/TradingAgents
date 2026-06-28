"""Config isolation: get/set must not leak nested-dict references."""

import copy
import unittest

import pytest

import tradingagents.default_config as default_config
from tradingagents.dataflows.config import get_config, set_config


@pytest.mark.unit
class DataflowsConfigIsolationTests(unittest.TestCase):
    def setUp(self):
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))

    def test_get_config_returns_deep_copy(self):
        cfg = get_config()
        cfg["data_vendors"]["core_stock_apis"] = "akshare"
        cfg["tool_vendors"]["get_stock_data"] = "akshare"

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "akshare")
        self.assertNotIn("get_stock_data", fresh["tool_vendors"])

    def test_set_config_does_not_alias_caller_nested_dicts(self):
        custom = copy.deepcopy(default_config.DEFAULT_CONFIG)
        custom["data_vendors"]["core_stock_apis"] = "akshare"
        custom["tool_vendors"]["get_stock_data"] = "akshare"

        set_config(custom)

        custom["data_vendors"]["core_stock_apis"] = "akshare"
        custom["tool_vendors"]["get_stock_data"] = "akshare"

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "akshare")
        self.assertEqual(fresh["tool_vendors"]["get_stock_data"], "akshare")

    def test_partial_nested_update_preserves_existing_defaults(self):
        set_config(
            {
                "data_vendors": {
                    "core_stock_apis": "akshare",
                }
            }
        )

        fresh = get_config()
        self.assertEqual(fresh["data_vendors"]["core_stock_apis"], "akshare")
        self.assertEqual(fresh["data_vendors"]["technical_indicators"], "akshare")
        self.assertEqual(fresh["data_vendors"]["fundamental_data"], "akshare")
        self.assertEqual(fresh["data_vendors"]["news_data"], "akshare")

    def test_nested_dict_updates_merge_one_level_deep(self):
        set_config({"tool_vendors": {"get_stock_data": "akshare"}})
        set_config({"tool_vendors": {"get_news": "akshare"}})

        fresh = get_config()
        self.assertEqual(fresh["tool_vendors"]["get_stock_data"], "akshare")
        self.assertEqual(fresh["tool_vendors"]["get_news"], "akshare")
