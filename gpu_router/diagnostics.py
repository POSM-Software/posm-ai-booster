"""Allowlisted synthetic telemetry and opt-in, advisory-only local diagnostics.

No AI model or remote connector is implemented. Identifiers are dropped, not
hashed. The provider protocol is for trusted application code, not a sandbox.
"""

import json
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Protocol, TextIO


STATUSES = frozenset({"simulated_success", "rejected", "reservation_rejected", "failed"})
CODES = frozenset({"no_events", "review_rejections", "review_simulated_failures",
                   "review_fallbacks", "diagnostic_unavailable",
                   "no_issue_detected_in_synthetic_summary"})


def _integer(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


@dataclass(frozen=True)
class SafeEvent:
    """Versioned schema; deliberately no arbitrary text or stable identifiers."""

    sequence: int
    status: str
    attempt_count: int
    predicted_ms: float
    cost_microusd: int
    schema_version: int = 1
    synthetic: bool = True

    def __post_init__(self) -> None:
        validate_event(self)


def validate_event(event: SafeEvent) -> None:
    # Exact types also reject user-defined __str__/serialization hooks.
    if type(event) is not SafeEvent:
        raise ValueError("Invalid event schema")
    if type(event.schema_version) is not int or event.schema_version != 1 or event.synthetic is not True:
        raise ValueError("Unsupported event schema or source")
    if not _integer(event.sequence, 1_000_000) or not _integer(event.attempt_count, 2):
        raise ValueError("Invalid event counter")
    if type(event.status) is not str or event.status not in STATUSES:
        raise ValueError("Invalid event status")
    if (type(event.predicted_ms) not in (int, float) or
            not 0 <= event.predicted_ms <= 10**15 or not isfinite(event.predicted_ms)):
        raise ValueError("Invalid predicted duration")
    if not _integer(event.cost_microusd, 10**15):
        raise ValueError("Invalid event cost")


def sanitize_result(result: dict, *, sequence: int) -> SafeEvent:
    """Project one simulation result onto an allowlist, without copying extras.

    No job IDs, worker IDs, model names, paths, reasons, prompts, payloads,
    exception strings or nested attempt data enter the output. Unknown fields
    are ignored; invalid required fields fail closed without echoing input.
    """
    if type(result) is not dict or result.get("synthetic") is not True:
        raise ValueError("Expected a synthetic simulation result")
    attempts = result.get("attempts")
    if type(attempts) is not list:
        raise ValueError("Invalid attempt collection")
    return SafeEvent(sequence=sequence, status=result.get("status"),
                     attempt_count=len(attempts),
                     predicted_ms=result.get("total_predicted_ms"),
                     cost_microusd=result.get("total_cost_microusd"))


def write_jsonl(events: tuple[SafeEvent, ...], stream: TextIO) -> None:
    """Write only the validated schema to a caller-owned local stream.

    Validate the complete batch before writing anything. No global logger,
    automatic file creation, external handler, or raw exception capture.
    """
    _validate_events(events)
    for event in events:
        stream.write(json.dumps(asdict(event), sort_keys=True, allow_nan=False) + "\n")


def _validate_events(events: tuple[SafeEvent, ...]) -> None:
    if type(events) is not tuple or len(events) > 10_000:
        raise ValueError("Expected a bounded immutable event batch")
    for event in events:
        validate_event(event)


@dataclass(frozen=True)
class Advisory:
    """Closed output vocabulary: no commands, URLs, routes, budgets or prose."""

    codes: tuple[str, ...]
    source: str = "local_rules_no_ai"
    advisory_only: bool = True

    def __post_init__(self) -> None:
        validate_advisory(self)


def validate_advisory(result: Advisory) -> None:
    if type(result) is not Advisory or result.advisory_only is not True:
        raise ValueError("Invalid advisory schema")
    if type(result.source) is not str or result.source != "local_rules_no_ai":
        raise ValueError("Unsupported diagnostic source")
    if (type(result.codes) is not tuple or not 1 <= len(result.codes) <= len(CODES) or
            any(type(code) is not str or code not in CODES for code in result.codes) or
            len(set(result.codes)) != len(result.codes)):
        raise ValueError("Invalid advisory codes")


class DiagnosticProvider(Protocol):
    """Trusted, in-process advisory extension point; not an execution sandbox.

    Any future model adapter needs separate review/authorization and an updated
    source schema. No runtime plugin loading or model adapter is shipped.
    """

    def diagnose(self, events: tuple[SafeEvent, ...]) -> Advisory: ...


class LocalDiagnostics:
    """Deterministic rules for synthetic summaries, not AI debugging."""

    def diagnose(self, events: tuple[SafeEvent, ...]) -> Advisory:
        _validate_events(events)
        codes = []
        if not events:
            codes.append("no_events")
        if any(e.status in ("rejected", "reservation_rejected") for e in events):
            codes.append("review_rejections")
        if any(e.status == "failed" for e in events):
            codes.append("review_simulated_failures")
        if any(e.attempt_count > 1 for e in events):
            codes.append("review_fallbacks")
        return Advisory(tuple(codes) or ("no_issue_detected_in_synthetic_summary",))


def diagnose(events: tuple[SafeEvent, ...], *, enabled: bool = False,
             provider: DiagnosticProvider | None = None) -> Advisory | None:
    """No provider is invoked without explicit True opt-in.

    Only sanitized immutable events are passed. Results are validated and
    returned, never applied to routing/accounting or interpreted as commands.
    """
    if type(enabled) is not bool:
        raise ValueError("Diagnostic opt-in must be a boolean")
    if not enabled:
        return None
    _validate_events(events)
    try:
        result = (provider if provider is not None else LocalDiagnostics()).diagnose(events)
    except Exception:
        # Never emit exception text/tracebacks, which may contain payloads,
        # credentials or paths. Do not catch process exit/interrupt signals.
        return Advisory(("diagnostic_unavailable",))
    validate_advisory(result)
    return result
