#!/usr/bin/env python3
"""Tests for prompt_user_for_local_build non-interactive behavior.

These complement existing tests by explicitly covering NO_INTERACTIVE env
variable handling to guard against regressions if TTY logic changes again.
"""

import os

from src.cli import container


def test_prompt_skipped_when_no_interactive(monkeypatch):
    monkeypatch.setenv("NO_INTERACTIVE", "1")
    # Simulate user would answer 'y' if prompted; should be ignored.
    monkeypatch.setenv("CI", "true")  # Reinforce non-interactive state
    decided = container.prompt_user_for_local_build()
    assert decided is False


def test_prompt_returns_true_on_y(monkeypatch):
    monkeypatch.delenv("NO_INTERACTIVE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    # Provide input 'y' to confirm positive path still works.
    monkeypatch.setenv("PYTEST_FAKE_INPUT", "y\n")  # if custom harness used
    monkeypatch.setattr("builtins.input", lambda _: "y")
    decided = container.prompt_user_for_local_build()
    assert decided is True


def test_prompt_returns_false_on_n(monkeypatch):
    monkeypatch.delenv("NO_INTERACTIVE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    decided = container.prompt_user_for_local_build()
    assert decided is False
