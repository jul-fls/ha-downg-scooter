"""Tests for config-entry startup behavior."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    """Verify that an offline scooter does not trigger HA setup backoff."""

    def test_setup_uses_best_effort_refresh(self) -> None:
        source = (ROOT / "custom_components/downg_scooter/__init__.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        setup = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_setup_entry"
        )
        called_methods = {
            node.func.attr
            for node in ast.walk(setup)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertIn("async_refresh", called_methods)
        self.assertNotIn("async_config_entry_first_refresh", called_methods)

    def test_coordinator_has_empty_initial_data(self) -> None:
        source = (
            ROOT / "custom_components/downg_scooter/coordinator.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "data"
                for target in node.targets
            )
        ]

        self.assertTrue(
            any(
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "ScooterData"
                for node in assignments
            )
        )

    def test_reauth_unloads_live_entry_before_pairing(self) -> None:
        source = (
            ROOT / "custom_components/downg_scooter/config_flow.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        reauth = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for node in node.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_step_reauth"
        )
        called_methods = {
            node.func.attr
            for node in ast.walk(reauth)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertIn("async_unload", called_methods)
