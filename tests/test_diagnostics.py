"""Security boundaries for the offline, advisory-only diagnostic extension."""

import ast
import io
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from gpu_router import diagnostics
from gpu_router.__main__ import demo
from gpu_router.diagnostics import (Advisory, LocalDiagnostics, SafeEvent, diagnose,
                                    sanitize_result, write_jsonl)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"synthetic": True, "status": "simulated_success", "attempts": [{}],
                    "total_predicted_ms": 3.5, "total_cost_microusd": 0}
        self.event = sanitize_result(self.raw, sequence=0)

    def test_exact_schema(self):
        output = io.StringIO()
        write_jsonl((self.event,), output)
        data = json.loads(output.getvalue())
        self.assertEqual(set(data), {"schema_version", "synthetic", "sequence", "status",
                                     "attempt_count", "predicted_ms", "cost_microusd"})
        self.assertEqual(data["schema_version"], 1)

    def test_all_identifiers_and_extra_fields_dropped(self):
        marker = "SECRET_customer_a_api-key_123_/home/customer/private"
        self.raw.update(job_id=marker, worker_id=marker, model=marker, path=marker,
                        prompt=marker, payload=marker, exception=marker,
                        api_key=marker, extra={"nested": marker}, reason=marker)
        self.raw["attempts"] = [{"decision": {"worker_id": marker}, "payload": marker}]
        output = io.StringIO()
        event = sanitize_result(self.raw, sequence=1)
        write_jsonl((event,), output)
        self.assertNotIn(marker, output.getvalue())
        self.assertNotIn(marker, repr(diagnose((event,), enabled=True)))
        self.assertNotIn("job_id", output.getvalue())

    def test_unknown_data_not_serialized_or_stringified(self):
        class Poison:
            def __str__(self):
                raise AssertionError("Should never stringify private objects")
            def __repr__(self):
                raise AssertionError("Should never represent private objects")
        self.raw["payload"] = Poison()
        self.raw["attempts"] = [Poison()]
        self.assertEqual(sanitize_result(self.raw, sequence=0), self.event)

    def test_invalid_status_does_not_echo_private_text(self):
        self.raw["status"] = "secret-prompt"
        with self.assertRaises(ValueError) as error:
            sanitize_result(self.raw, sequence=0)
        self.assertNotIn("secret-prompt", str(error.exception))

    def test_invalid_schema_fields(self):
        for fields in ({"sequence": True}, {"sequence": -1}, {"sequence": 1_000_001},
                       {"schema_version": True}, {"schema_version": 2}, {"synthetic": False},
                       {"attempt_count": 3}, {"attempt_count": True}, {"status": []},
                       {"predicted_ms": float("nan")}, {"predicted_ms": float("inf")},
                       {"predicted_ms": "secret"}, {"predicted_ms": True},
                       {"predicted_ms": -1}, {"predicted_ms": 10**999},
                       {"cost_microusd": 1.5}, {"cost_microusd": True},
                       {"cost_microusd": -1}, {"cost_microusd": 10**16}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(self.event, **fields)

    def test_raw_schema_validation(self):
        for raw in (None, {}, {**self.raw, "synthetic": 1},
                    {**self.raw, "attempts": ()}, {**self.raw, "attempts": [0, 0, 0]}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sanitize_result(raw, sequence=0)

    def test_disabled_diagnostics_do_not_invoke_provider(self):
        provider = Mock()
        self.assertIsNone(diagnose((self.event,), provider=provider))
        provider.diagnose.assert_not_called()

    def test_non_boolean_opt_in_rejected(self):
        provider = Mock()
        for enabled in (1, "true", [], None):
            with self.assertRaises(ValueError):
                diagnose((self.event,), enabled=enabled, provider=provider)
        provider.diagnose.assert_not_called()

    def test_enabled_provider_only_receives_sanitized_events(self):
        provider = Mock()
        provider.diagnose.return_value = Advisory(("review_rejections",))
        result = diagnose((self.event,), enabled=True, provider=provider)
        provider.diagnose.assert_called_once_with((self.event,))
        self.assertTrue(result.advisory_only)

    def test_raw_input_rejected_before_provider(self):
        provider = Mock()
        for events in ([self.event], (self.raw,), (self.event,) * 10001):
            with self.assertRaises(ValueError):
                diagnose(events, enabled=True, provider=provider)
        provider.diagnose.assert_not_called()

    def test_events_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.event.cost_microusd = 100

    def test_forged_event_revalidated_before_output(self):
        object.__setattr__(self.event, "status", "secret")
        output = io.StringIO()
        with self.assertRaises(ValueError):
            write_jsonl((self.event,), output)
        self.assertEqual(output.getvalue(), "")
        with self.assertRaises(ValueError):
            diagnose((self.event,), enabled=True)

    def test_invalid_batch_writes_nothing(self):
        output = io.StringIO()
        with self.assertRaises(ValueError):
            write_jsonl((self.event, self.raw), output)
        self.assertEqual(output.getvalue(), "")

    def test_local_diagnostics_deterministic_and_conservative(self):
        events = tuple(sanitize_result(r, sequence=i) for i, r in enumerate(demo()["results"]))
        result = diagnose(events, enabled=True)
        self.assertEqual(result, diagnose(events, enabled=True))
        self.assertEqual(result.source, "local_rules_no_ai")
        self.assertEqual(result.codes, ("review_rejections", "review_fallbacks"))
        self.assertEqual(diagnose((), enabled=True).codes, ("no_events",))
        self.assertEqual(diagnose((replace(self.event, status="failed"),), enabled=True).codes,
                         ("review_simulated_failures",))
        self.assertEqual(diagnose((self.event,), enabled=True).codes,
                         ("no_issue_detected_in_synthetic_summary",))

    def test_advisory_schema_rejects_commands_and_free_text(self):
        for codes in (("rm -rf /",), ("https://example.com/upload",), (),
                      ("review_rejections", "review_rejections"), ["review_rejections"]):
            with self.assertRaises(ValueError):
                Advisory(codes)
        with self.assertRaises(ValueError):
            Advisory(("review_rejections",), advisory_only=False)
        with self.assertRaises(ValueError):
            Advisory(("review_rejections",), source="remote_ai")

    def test_provider_exception_is_contained_without_secret_text(self):
        provider = Mock()
        provider.diagnose.side_effect = RuntimeError("secret_key customer /private/path prompt")
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = diagnose((self.event,), enabled=True, provider=provider)
        self.assertEqual(result, Advisory(("diagnostic_unavailable",)))
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("secret_key", repr(result))

    def test_invalid_provider_output_not_returned_or_executed(self):
        provider = Mock()
        provider.diagnose.return_value = {"command": "touch /tmp/unsafe", "budget": 999999}
        with self.assertRaises(ValueError):
            diagnose((self.event,), enabled=True, provider=provider)

    def test_local_diagnostics_have_no_execution_or_network_imports(self):
        tree = ast.parse(Path(diagnostics.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"exec", "eval", "compile", "__import__", "open"})
        self.assertEqual(imports, {"json", "dataclasses", "math", "typing"})

    def test_diagnostics_do_not_change_demo_routes_or_accounting(self):
        before = demo()
        events = tuple(sanitize_result(r, sequence=i) for i, r in enumerate(before["results"]))
        diagnose(events, enabled=True)
        self.assertEqual(before, demo())

    def test_cli_opt_in_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            baseline = subprocess.run([sys.executable, "-m", "gpu_router", "--json"],
                                      check=True, capture_output=True, text=True)
            active = subprocess.run([sys.executable, "-m", "gpu_router", "--json", "--diagnostics",
                                     "--log-jsonl", str(log)],
                                    check=True, capture_output=True, text=True)
            default = json.loads(baseline.stdout)
            enabled = json.loads(active.stdout)
            self.assertNotIn("diagnostics", default)
            self.assertEqual(enabled.pop("diagnostics")["source"], "local_rules_no_ai")
            self.assertEqual(default, enabled)
            saved = log.read_text()
            self.assertEqual(len(saved.splitlines()), 6)
            self.assertNotIn("job_id", saved)
            self.assertNotIn("aws-gpu", saved)
            repeat = subprocess.run([sys.executable, "-m", "gpu_router", "--log-jsonl", str(log)],
                                    capture_output=True, text=True)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual(log.read_text(), saved)


if __name__ == "__main__":
    unittest.main()
