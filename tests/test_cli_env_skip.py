"""Tests for env-driven CLI behavior (#897, #873).

The config-layer override (TRADINGAGENTS_* -> DEFAULT_CONFIG) is covered by
test_env_overrides.py. These tests cover the CLI layer: an env-configured
provider/model/language must skip its interactive prompt and use the value.
"""

import os
import unittest
from unittest import mock

import pytest


@pytest.mark.unit
class TestProviderDefaultUrl(unittest.TestCase):
    def test_deepseek_resolves(self):
        from cli.utils import provider_default_url
        self.assertEqual(provider_default_url("DeepSeek"), "https://api.deepseek.com")

    def test_unknown_provider_returns_none(self):
        from cli.utils import provider_default_url
        self.assertIsNone(provider_default_url("not-a-provider"))


@pytest.mark.unit
class TestCliSkipsPromptsFromEnv(unittest.TestCase):
    def test_env_config_skips_llm_prompts(self):
        import cli.main as m

        env = {
            "TRADINGAGENTS_DEEP_THINK_LLM": "deepseek-v4-pro",
            "TRADINGAGENTS_QUICK_THINK_LLM": "deepseek-v4-flash",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://api.deepseek.com",
        }
        fake_cfg = dict(m.DEFAULT_CONFIG)
        fake_cfg.update({
            "llm_provider": "deepseek",
            "backend_url": "https://api.deepseek.com",
            "quick_think_llm": "deepseek-v4-flash",
            "deep_think_llm": "deepseek-v4-pro",
        })

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(m, "DEFAULT_CONFIG", fake_cfg), \
             mock.patch.object(m, "fetch_announcements", return_value=None), \
             mock.patch.object(m, "display_announcements"), \
             mock.patch.object(m, "get_ticker", return_value="AAPL"), \
             mock.patch.object(m, "get_analysis_date", return_value="2026-05-29"), \
             mock.patch.object(m, "select_analysts", return_value=[]), \
             mock.patch.object(m, "select_research_depth", return_value=1), \
             mock.patch.object(m, "ensure_api_key") as ensure_key, \
             mock.patch.object(m, "select_llm_provider") as prompt_provider, \
             mock.patch.object(m, "select_shallow_thinking_agent") as prompt_quick, \
             mock.patch.object(m, "select_deep_thinking_agent") as prompt_deep:
            sel = m.get_user_selections()

        # None of the LLM selection prompts should have been shown.
        prompt_provider.assert_not_called()
        prompt_quick.assert_not_called()
        prompt_deep.assert_not_called()
        # API key is still verified for the configured provider.
        ensure_key.assert_called_once()

        # The env values flow into the returned selections.
        self.assertEqual(sel["llm_provider"], "deepseek")
        self.assertEqual(sel["backend_url"], "https://api.deepseek.com")
        self.assertEqual(sel["shallow_thinker"], "deepseek-v4-flash")
        self.assertEqual(sel["deep_thinker"], "deepseek-v4-pro")
        # Output language is hardcoded to Chinese; no prompt is shown.
        self.assertEqual(sel["output_language"], "Chinese")


if __name__ == "__main__":
    unittest.main()
