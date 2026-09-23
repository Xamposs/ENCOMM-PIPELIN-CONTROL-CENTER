"""Session 008 — TaskPanel usage/visibility lines (brief §34)."""

from __future__ import annotations

from encomm_pcc.ui.panels import _usage_lines


def test_hermes_stream_tokens_are_shown_as_reported():
    lines = _usage_lines(
        {"stream": {"model": "deepseek/deepseek-v4.1-flash",
                    "tokens": {"input": 10, "output": 5, "total": 15}}}
    )
    assert "model          : deepseek/deepseek-v4.1-flash" in lines
    assert any(line.startswith("usage(hermes)") and "input=10" in line for line in lines)


def test_codex_stream_usage_is_shown_as_reported():
    lines = _usage_lines(
        {"stream": {"input_tokens": 100, "cached_input_tokens": 40,
                    "output_tokens": 25, "tool_use_count": 3}}
    )
    line = next(ln for ln in lines if ln.startswith("usage(codex)"))
    assert "input_tokens=100" in line
    assert "cached_input_tokens=40" in line
    assert "output_tokens=25" in line
    assert "tool_calls     : 3" in lines


def test_absent_usage_is_not_invented():
    assert _usage_lines({}) == []
    assert _usage_lines({"stream": {}}) == []
    assert _usage_lines({"stream": "not-a-dict"}) == []


def test_unlike_providers_are_never_merged():
    hermes = _usage_lines({"stream": {"tokens": {"total": 15}}})
    codex = _usage_lines({"stream": {"input_tokens": 7}})
    assert any("usage(hermes)" in ln for ln in hermes)
    assert not any("usage(codex)" in ln for ln in hermes)
    assert any("usage(codex)" in ln for ln in codex)
    assert not any("usage(hermes)" in ln for ln in codex)
