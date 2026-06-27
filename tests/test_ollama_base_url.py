"""Tests for DeepSeek base URL resolution.

Now that only DeepSeek is supported as the LLM provider, this file
tests that the resolver returns DeepSeek's default endpoint.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_client():
    import tradingagents.llm_clients.openai_client as mod
    return importlib.reload(mod)


def test_resolver_returns_deepseek_default(monkeypatch):
    mod = _reload_client()
    assert mod._resolve_provider_base_url("deepseek") == "https://api.deepseek.com"


def test_resolver_returns_none_for_unknown(monkeypatch):
    mod = _reload_client()
    assert mod._resolve_provider_base_url("unknown") is None
