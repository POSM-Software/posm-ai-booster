"""Deterministic, fail-closed routing against explicitly synthetic telemetry.

Dollar budgets use integer microdollars (1 USD = 1_000_000 microdollars).
This module never contacts or executes on a worker.
"""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from math import isfinite
from threading import Lock
from typing import Literal


def number(name: str, value: float, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number")
    # Bound integer magnitudes before math.isfinite converts them to float.
    if value < 0 or value > 1e12 or not isfinite(value) or (positive and value < 1e-9):
        raise ValueError(f"{name} is outside the supported finite range")


def money(name: str, value: int) -> None:
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError(f"{name} must be a nonnegative integer number of microdollars")


def label(name: str, value: str, max_length: int = 128) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must contain 1..{max_length} characters")


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    model: str
    work_units: float
    memory_gb: float
    input_mb: float = 0
    output_mb: float = 0
    privacy: Literal["local_only", "remote_allowed"] = "local_only"
    allow_remote: bool = False
    deadline_ms: float = 60_000
    max_cost_microusd: int = 0

    def __post_init__(self) -> None:
        for name in ("id", "kind", "model"):
            label(name, getattr(self, name))
        for name in ("work_units", "memory_gb", "input_mb", "output_mb", "deadline_ms"):
            number(name, getattr(self, name))
        if self.privacy not in ("local_only", "remote_allowed"):
            raise ValueError("Unknown privacy policy")
        if type(self.allow_remote) is not bool:
            raise ValueError("allow_remote must be a boolean")
        money("max_cost_microusd", self.max_cost_microusd)


@dataclass(frozen=True)
class Worker:
    id: str
    location: Literal["local", "remote"]
    kinds: tuple[str, ...]
    models: tuple[str, ...]
    memory_gb: float
    units_per_second: float
    observed_at: float
    healthy: bool = True
    queue_ms: float = 0
    rtt_ms: float = 0
    upload_mbps: float = 100
    download_mbps: float = 100
    startup_ms: float = 0
    compute_microusd_per_second: int = 0
    egress_microusd_per_mb: int = 0
    request_microusd: int = 0

    def __post_init__(self) -> None:
        label("worker id", self.id)
        if self.location not in ("local", "remote"):
            raise ValueError("Unknown worker location")
        if type(self.healthy) is not bool:
            raise ValueError("healthy must be a boolean")
        for name in ("kinds", "models"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{name} must be a nonempty immutable tuple")
            for value in values:
                label(name, value)
        for name in ("memory_gb", "observed_at", "queue_ms", "rtt_ms", "startup_ms"):
            number(name, getattr(self, name))
        for name in ("units_per_second", "upload_mbps", "download_mbps"):
            number(name, getattr(self, name), positive=True)
        for name in ("compute_microusd_per_second", "egress_microusd_per_mb", "request_microusd"):
            money(name, getattr(self, name))
        if self.location == "local" and any((self.rtt_ms, self.egress_microusd_per_mb)):
            raise ValueError("Local fixtures cannot include network RTT or egress pricing")


@dataclass(frozen=True)
class Policy:
    telemetry_ttl_seconds: float = 30
    min_remote_saving_ms: float = 100
    min_remote_saving_fraction: float = 0.20
    prediction_padding_fraction: float = 0.10

    def __post_init__(self) -> None:
        for name in ("telemetry_ttl_seconds", "min_remote_saving_ms",
                     "min_remote_saving_fraction", "prediction_padding_fraction"):
            number(name, getattr(self, name))
        if self.min_remote_saving_fraction > 1 or self.prediction_padding_fraction > 10:
            raise ValueError("Unsupported fractional policy")


@dataclass(frozen=True)
class Estimate:
    worker_id: str
    location: str
    latency_ms: float
    cost_microusd: int
    queue_ms: float
    network_ms: float
    startup_ms: float
    compute_ms: float
    padding_ms: float


@dataclass(frozen=True)
class Evaluation:
    worker_id: str
    eligible: bool
    reasons: tuple[str, ...]
    estimate: Estimate | None


@dataclass(frozen=True)
class Decision:
    job_id: str
    status: str
    reason: str
    selected: Estimate | None
    evaluations: tuple[Evaluation, ...]
    synthetic: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class BudgetLedger:
    """In-memory atomic reservations. This is not billing or durable accounting.

    IDs are never reusable, including after cancellation. Spending is bounded
    by the reserved amount. Real execution must enforce the reservation at the
    provider, or cannot promise a hard monetary cap from an estimate alone.
    """

    def __init__(self, limit_microusd: int):
        money("limit_microusd", limit_microusd)
        self._limit_microusd = limit_microusd
        self._spent = 0
        self._reservations: dict[str, int] = {}
        self._used_ids: set[str] = set()
        self._lock = Lock()

    @property
    def limit_microusd(self) -> int:
        return self._limit_microusd

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            reserved = sum(self._reservations.values())
            return {"limit_microusd": self.limit_microusd, "spent_microusd": self._spent,
                    "reserved_microusd": reserved,
                    "available_microusd": self.limit_microusd - self._spent - reserved}

    def reserve(self, reservation_id: str, amount: int) -> bool:
        label("reservation_id", reservation_id, max_length=256)
        money("amount", amount)
        with self._lock:
            if reservation_id in self._used_ids:
                return False
            if self._spent + sum(self._reservations.values()) + amount > self.limit_microusd:
                return False
            self._reservations[reservation_id] = amount
            self._used_ids.add(reservation_id)
            return True

    def settle(self, reservation_id: str, actual_microusd: int) -> None:
        money("actual_microusd", actual_microusd)
        with self._lock:
            if reservation_id not in self._reservations:
                raise ValueError("Unknown or already settled reservation")
            if actual_microusd > self._reservations[reservation_id]:
                raise ValueError("Settlement exceeds reservation; reservation remains held")
            self._spent += actual_microusd
            del self._reservations[reservation_id]


class Router:
    def __init__(self, workers: tuple[Worker, ...], policy: Policy | None = None):
        self.workers = tuple(workers)
        self.policy = policy or Policy()
        if len({w.id for w in self.workers}) != len(self.workers):
            raise ValueError("Worker IDs must be unique")

    def estimate(self, job: Job, worker: Worker) -> Estimate:
        # Exact rational arithmetic keeps tiny nonzero costs from disappearing
        # beside a fixed fee, and is independent of ambient Decimal precision.
        q = lambda value: Fraction(str(value))
        compute_exact = q(job.work_units) / q(worker.units_per_second) * 1000
        # MB is decimal megabytes; Mbps is decimal megabits per second.
        network_exact = (q(worker.rtt_ms) + q(job.input_mb) * 8 / q(worker.upload_mbps) * 1000
                         + q(job.output_mb) * 8 / q(worker.download_mbps) * 1000) if worker.location == "remote" else Fraction(0)
        unpadded = q(worker.queue_ms) + q(worker.startup_ms) + compute_exact + network_exact
        padding = unpadded * q(self.policy.prediction_padding_fraction)
        # Conservatively price the whole padded estimate, even transfer/queue time.
        cost = ((unpadded + padding) / 1000 * worker.compute_microusd_per_second
                + q(job.output_mb) * worker.egress_microusd_per_mb + worker.request_microusd)
        ceiling_cost = -(-cost.numerator // cost.denominator)
        return Estimate(worker.id, worker.location, float(unpadded + padding), ceiling_cost,
                        worker.queue_ms, float(network_exact), worker.startup_ms,
                        float(compute_exact), float(padding))

    def plan(self, job: Job, *, now: float, ledger: BudgetLedger,
             exclude: frozenset[str] = frozenset()) -> Decision:
        number("now", now)
        available = ledger.snapshot()["available_microusd"]
        evaluations = []
        for worker in sorted(self.workers, key=lambda w: w.id):
            reasons = []
            if worker.id in exclude:
                reasons.append("excluded_after_failure")
            if worker.location == "remote":
                if job.privacy == "local_only":
                    reasons.append("privacy_requires_local")
                if not job.allow_remote:
                    reasons.append("remote_not_authorized")
            if not worker.healthy:
                reasons.append("worker_unhealthy")
            age = now - worker.observed_at
            if age < 0:
                reasons.append("telemetry_from_future")
            elif age > self.policy.telemetry_ttl_seconds:
                reasons.append("telemetry_stale")
            if job.kind not in worker.kinds:
                reasons.append("unsupported_workload")
            if job.model not in worker.models:
                reasons.append("model_unavailable")
            if job.memory_gb > worker.memory_gb:
                reasons.append("insufficient_memory")
            if reasons:
                evaluations.append(Evaluation(worker.id, False, tuple(reasons), None))
                continue
            estimate = self.estimate(job, worker)
            if not isfinite(estimate.latency_ms):
                reasons.append("invalid_prediction")
            if estimate.latency_ms > job.deadline_ms:
                reasons.append("predicted_deadline_miss")
            if estimate.cost_microusd > job.max_cost_microusd:
                reasons.append("job_budget_exceeded")
            if estimate.cost_microusd > available:
                reasons.append("session_budget_exceeded")
            evaluations.append(Evaluation(worker.id, not reasons, tuple(reasons), estimate))
        feasible = [e.estimate for e in evaluations if e.eligible and e.estimate is not None]
        if not feasible:
            return Decision(job.id, "rejected", "No worker satisfies every constraint", None, tuple(evaluations))
        key = lambda e: (e.latency_ms, e.cost_microusd, e.worker_id)
        local = sorted((e for e in feasible if e.location == "local"), key=key)
        remote = sorted((e for e in feasible if e.location == "remote"), key=key)
        if local:
            selected = local[0]
            reason = "Local route satisfies constraints"
            if remote:
                saving = local[0].latency_ms - remote[0].latency_ms
                threshold = max(self.policy.min_remote_saving_ms,
                                local[0].latency_ms * self.policy.min_remote_saving_fraction)
                if saving > 0 and saving >= threshold:
                    selected = remote[0]
                    reason = "Authorized remote route exceeds the required latency saving"
                else:
                    reason = "Local preferred: remote latency saving is below policy threshold"
        else:
            selected = remote[0]
            reason = "Authorized remote route selected; no eligible local worker"
        return Decision(job.id, "planned", reason, selected, tuple(evaluations))


def run_simulated(router: Router, job: Job, *, now: float, ledger: BudgetLedger,
                  fail_workers: frozenset[str] = frozenset()) -> dict:
    """Simulate one attempt, and at most one local fallback after remote failure.

    No payload exists or is sent. A failed simulated attempt consumes its full
    predicted time and cost. Fallback must fit the original TOTAL job limits.
    Reservations are rechecked atomically. No remote retry is performed.
    """
    number("now", now)
    attempts = []
    total_ms = 0.0
    total_cost = 0
    current_job = job
    excluded: frozenset[str] = frozenset()
    for attempt_index in range(2):
        current_now = now + total_ms / 1000
        if attempt_index and current_now > 1e12:
            return {"synthetic": True, "status": "failed", "reason": "simulation_clock_limit",
                    "attempts": attempts, "total_predicted_ms": total_ms,
                    "total_cost_microusd": total_cost}
        decision = router.plan(current_job, now=current_now, ledger=ledger, exclude=excluded)
        selected = decision.selected
        if selected is None:
            return {"synthetic": True, "status": "rejected" if not attempts else "failed",
                    "attempts": attempts, "last_decision": decision.to_dict(),
                    "total_predicted_ms": total_ms, "total_cost_microusd": total_cost}
        # The reservation ID deliberately does not change on retries of the same job.
        reservation_id = f"{job.id}:{attempt_index}"
        if not ledger.reserve(reservation_id, selected.cost_microusd):
            return {"synthetic": True, "status": "reservation_rejected",
                    "reason": "Budget changed or this job attempt was already submitted",
                    "attempts": attempts, "total_predicted_ms": total_ms,
                    "total_cost_microusd": total_cost}
        failed = selected.worker_id in fail_workers
        ledger.settle(reservation_id, selected.cost_microusd)
        total_ms += selected.latency_ms
        total_cost += selected.cost_microusd
        attempts.append({"outcome": "simulated_failure" if failed else "simulated_success",
                         "decision": decision.to_dict()})
        if not failed:
            return {"synthetic": True, "status": "simulated_success", "attempts": attempts,
                    "total_predicted_ms": total_ms, "total_cost_microusd": total_cost}
        if selected.location == "local" or attempt_index == 1:
            break
        excluded = frozenset({selected.worker_id})
        current_job = replace(job, privacy="local_only", allow_remote=False,
                              deadline_ms=max(0, job.deadline_ms - total_ms),
                              max_cost_microusd=max(0, job.max_cost_microusd - total_cost))
    return {"synthetic": True, "status": "failed", "attempts": attempts,
            "total_predicted_ms": total_ms, "total_cost_microusd": total_cost}
