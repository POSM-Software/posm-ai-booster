import itertools
import json
import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import localcontext

from gpu_router import BudgetLedger, Job, Policy, Router, Worker, run_simulated
from gpu_router.__main__ import demo


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.local = Worker("local", "local", ("inference",), ("m",), 16, 10, self.now)
        self.remote = Worker("remote", "remote", ("inference",), ("m",), 24, 100, self.now,
                             rtt_ms=10, compute_microusd_per_second=1_000)
        self.job = Job("j", "inference", "m", 10, 8, privacy="remote_allowed",
                       allow_remote=True, deadline_ms=10_000, max_cost_microusd=5_000)
        self.router = Router((self.local, self.remote), Policy(prediction_padding_fraction=0))
        self.ledger = BudgetLedger(10_000)

    def plan(self, job=None, router=None, ledger=None, now=None):
        return (router or self.router).plan(job or self.job, now=self.now if now is None else now,
                                           ledger=ledger or self.ledger)

    def reasons(self, decision, worker="remote"):
        return next(e.reasons for e in decision.evaluations if e.worker_id == worker)

    def test_authorized_fast_remote(self):
        self.assertEqual(self.plan().selected.worker_id, "remote")

    def test_defaults_are_local_only_and_zero_budget(self):
        job = Job("default", "inference", "m", 10, 8)
        self.assertEqual(self.plan(job).selected.worker_id, "local")

    def test_local_only_overrides_allow_remote(self):
        decision = self.plan(replace(self.job, privacy="local_only"))
        self.assertEqual(decision.selected.worker_id, "local")
        self.assertIn("privacy_requires_local", self.reasons(decision))

    def test_remote_policy_requires_explicit_opt_in(self):
        decision = self.plan(replace(self.job, allow_remote=False))
        self.assertEqual(decision.selected.worker_id, "local")
        self.assertIn("remote_not_authorized", self.reasons(decision))

    def test_memory_capacity_boundary(self):
        self.assertEqual(self.plan(replace(self.job, memory_gb=24)).selected.worker_id, "remote")
        self.assertIsNone(self.plan(replace(self.job, memory_gb=24.0001)).selected)

    def test_model_and_workload_fail_closed(self):
        for field in ("kind", "model"):
            with self.subTest(field=field):
                decision = self.plan(replace(self.job, **{field: "unknown"}))
                self.assertEqual(decision.status, "rejected")
                self.assertIsNone(decision.selected)

    def test_unhealthy_worker_rejected(self):
        router = Router((self.local, replace(self.remote, healthy=False)))
        self.assertEqual(self.plan(router=router).selected.worker_id, "local")
        self.assertIn("worker_unhealthy", self.reasons(self.plan(router=router)))

    def test_stale_boundary_and_future_timestamp(self):
        self.assertIsNotNone(self.plan(now=130).selected)
        self.assertIsNone(self.plan(now=130.001).selected)
        self.assertIsNone(self.plan(now=99.999).selected)
        self.assertIn("telemetry_from_future", self.reasons(self.plan(now=99.999)))

    def test_deadline_includes_every_component(self):
        w = replace(self.remote, queue_ms=100, startup_ms=200, rtt_ms=10,
                    upload_mbps=8, download_mbps=8)
        router = Router((w,), Policy(prediction_padding_fraction=0))
        job = replace(self.job, input_mb=1, output_mb=2, max_cost_microusd=10_000)
        estimate = router.estimate(job, w)
        self.assertEqual(estimate.latency_ms, 3410)
        self.assertIsNotNone(self.plan(replace(job, deadline_ms=3410), router=router).selected)
        self.assertIsNone(self.plan(replace(job, deadline_ms=3409.999), router=router).selected)

    def test_prediction_padding_is_applied(self):
        router = Router((self.remote,), Policy(prediction_padding_fraction=0.1))
        self.assertAlmostEqual(router.estimate(self.job, self.remote).latency_ms, 121)

    def test_transfer_dominated_prefers_local(self):
        decision = self.plan(replace(self.job, input_mb=100))
        self.assertEqual(decision.selected.worker_id, "local")

    def test_cold_start_can_remove_remote_advantage(self):
        router = Router((self.local, replace(self.remote, startup_ms=2_000)))
        self.assertEqual(self.plan(router=router).selected.worker_id, "local")

    def test_cost_contains_egress_request_and_rounds_up(self):
        worker = replace(self.remote, egress_microusd_per_mb=10, request_microusd=3,
                         compute_microusd_per_second=1)
        estimate = self.router.estimate(replace(self.job, output_mb=0.5), worker)
        self.assertEqual(estimate.cost_microusd, 9)

    def test_tiny_nonzero_cost_above_fixed_fee_rounds_up(self):
        worker = replace(self.remote, units_per_second=1, rtt_ms=0,
                         compute_microusd_per_second=1, request_microusd=100)
        estimate = self.router.estimate(replace(self.job, work_units=1e-30), worker)
        self.assertEqual(estimate.cost_microusd, 101)

    def test_cost_does_not_depend_on_ambient_decimal_precision(self):
        worker = replace(self.remote, compute_microusd_per_second=0, request_microusd=111)
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(self.router.estimate(self.job, worker).cost_microusd, 111)

    def test_job_and_session_budget_exact_boundary(self):
        self.assertEqual(self.plan(replace(self.job, max_cost_microusd=110)).selected.worker_id, "remote")
        self.assertEqual(self.plan(replace(self.job, max_cost_microusd=109)).selected.worker_id, "local")
        self.assertEqual(self.plan(ledger=BudgetLedger(110)).selected.worker_id, "remote")
        self.assertEqual(self.plan(ledger=BudgetLedger(109)).selected.worker_id, "local")

    def test_relative_remote_saving_boundary(self):
        worker = replace(self.remote, rtt_ms=0, units_per_second=12.5)
        router = Router((self.local, worker), Policy(prediction_padding_fraction=0))
        self.assertEqual(self.plan(router=router).selected.worker_id, "remote")
        router = Router((self.local, replace(worker, queue_ms=0.001)), router.policy)
        self.assertEqual(self.plan(router=router).selected.worker_id, "local")

    def test_absolute_remote_saving_boundary(self):
        job = replace(self.job, work_units=1)
        worker = replace(self.remote, rtt_ms=0, units_per_second=20)
        policy = Policy(prediction_padding_fraction=0, min_remote_saving_ms=50,
                        min_remote_saving_fraction=0)
        router = Router((self.local, worker), policy)
        self.assertEqual(self.plan(job, router=router).selected.worker_id, "remote")
        router = Router((self.local, replace(worker, queue_ms=0.001)), policy)
        self.assertEqual(self.plan(job, router=router).selected.worker_id, "local")

    def test_equal_latency_never_remote_with_zero_threshold(self):
        worker = replace(self.remote, units_per_second=10, rtt_ms=0)
        router = Router((self.local, worker), Policy(prediction_padding_fraction=0,
                                                    min_remote_saving_ms=0, min_remote_saving_fraction=0))
        self.assertEqual(self.plan(router=router).selected.worker_id, "local")

    def test_no_feasible_local_allows_authorized_remote(self):
        self.assertEqual(self.plan(replace(self.job, memory_gb=20)).selected.worker_id, "remote")

    def test_deterministic_ties_and_input_order(self):
        remote_a = replace(self.remote, id="a")
        remote_b = replace(self.remote, id="b")
        for workers in itertools.permutations((self.local, remote_a, remote_b)):
            self.assertEqual(self.plan(router=Router(workers)).selected.worker_id, "a")

    def test_cost_breaks_latency_tie(self):
        expensive = replace(self.remote, id="a", request_microusd=1)
        cheap = replace(self.remote, id="b")
        self.assertEqual(self.plan(router=Router((expensive, cheap))).selected.worker_id, "b")

    def test_duplicate_workers_rejected(self):
        with self.assertRaises(ValueError):
            Router((self.local, self.local))

    def test_invalid_numeric_and_policy_inputs(self):
        for value in (float("nan"), float("inf"), -1, True, "1", 1e13, 10**400):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    replace(self.job, input_mb=value)
        for field in ("units_per_second", "upload_mbps", "download_mbps"):
            for value in (0, -1, 1e-300):
                with self.assertRaises(ValueError):
                    replace(self.remote, **{field: value})
        with self.assertRaises(ValueError):
            replace(self.job, privacy="unknown")
        with self.assertRaises(ValueError):
            replace(self.job, allow_remote=1)
        with self.assertRaises(ValueError):
            replace(self.job, max_cost_microusd=0.5)
        with self.assertRaises(ValueError):
            Policy(min_remote_saving_fraction=1.01)

    def test_no_workers_returns_rejection(self):
        self.assertEqual(self.plan(router=Router(())).status, "rejected")

    def test_rejection_never_spends(self):
        result = run_simulated(self.router, replace(self.job, memory_gb=100), now=self.now, ledger=self.ledger)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.ledger.snapshot()["spent_microusd"], 0)

    def test_remote_failure_falls_back_to_local(self):
        result = run_simulated(self.router, self.job, now=self.now, ledger=self.ledger,
                               fail_workers=frozenset({"remote"}))
        self.assertEqual(result["status"], "simulated_success")
        self.assertEqual([a["decision"]["selected"]["worker_id"] for a in result["attempts"]], ["remote", "local"])
        self.assertEqual(result["total_cost_microusd"], 110)
        self.assertEqual(result["total_predicted_ms"], 1110)

    def test_failure_fallback_cannot_reset_deadline(self):
        result = run_simulated(self.router, replace(self.job, deadline_ms=1000), now=self.now,
                               ledger=self.ledger, fail_workers=frozenset({"remote"}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 1)

    def test_failure_fallback_cannot_reset_budget(self):
        local = replace(self.local, compute_microusd_per_second=100)
        router = Router((local, self.remote), self.router.policy)
        result = run_simulated(router, replace(self.job, max_cost_microusd=150), now=self.now,
                               ledger=self.ledger, fail_workers=frozenset({"remote"}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(self.ledger.snapshot()["spent_microusd"], 110)

    def test_no_unsafe_remote_retry_when_local_unavailable(self):
        router = Router((self.remote, replace(self.remote, id="remote2")), self.router.policy)
        result = run_simulated(router, self.job, now=self.now, ledger=self.ledger,
                               fail_workers=frozenset({"remote"}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 1)

    def test_local_failure_not_retried(self):
        result = run_simulated(self.router, replace(self.job, privacy="local_only"), now=self.now,
                               ledger=self.ledger, fail_workers=frozenset({"local"}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 1)

    def test_fallback_clock_limit_returns_structured_failure(self):
        router = Router((replace(self.local, observed_at=1e12), replace(self.remote, observed_at=1e12)))
        result = run_simulated(router, self.job, now=1e12, ledger=self.ledger,
                               fail_workers=frozenset({"remote"}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "simulation_clock_limit")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(self.ledger.snapshot()["reserved_microusd"], 0)

    def test_simulation_validates_clock_before_arithmetic(self):
        for now in (True, 10**400, "100", float("nan"), float("inf"), -1):
            with self.subTest(now=now):
                before = self.ledger.snapshot()
                with self.assertRaises(ValueError):
                    run_simulated(self.router, self.job, now=now, ledger=self.ledger)
                self.assertEqual(self.ledger.snapshot(), before)

    def test_duplicate_job_is_not_billed_twice(self):
        run_simulated(self.router, self.job, now=self.now, ledger=self.ledger)
        before = self.ledger.snapshot()
        result = run_simulated(self.router, self.job, now=self.now, ledger=self.ledger)
        self.assertEqual(result["status"], "reservation_rejected")
        self.assertEqual(self.ledger.snapshot(), before)

    def test_long_valid_job_id_executes(self):
        result = run_simulated(self.router, replace(self.job, id="x" * 128), now=self.now, ledger=self.ledger)
        self.assertEqual(result["status"], "simulated_success")

    def test_demo_is_deterministic_and_json_serializable(self):
        self.assertEqual(demo(), demo())
        json.dumps(demo(), allow_nan=False)

    def test_seeded_scenarios_preserve_safety_invariants(self):
        rng = random.Random(187)
        for i in range(1_000):
            with self.subTest(scenario=i):
                job = replace(self.job, id=f"random-{i}", work_units=rng.uniform(0, 100),
                              input_mb=rng.uniform(0, 100), memory_gb=rng.choice((8, 16, 20, 40)),
                              privacy=rng.choice(("local_only", "remote_allowed")),
                              allow_remote=rng.choice((True, False)),
                              deadline_ms=rng.uniform(0, 20_000), max_cost_microusd=rng.randrange(5_000))
                ledger = BudgetLedger(rng.randrange(5_000))
                result = run_simulated(self.router, job, now=self.now, ledger=ledger,
                                       fail_workers=frozenset({"remote"}) if rng.random() < 0.5 else frozenset())
                self.assertLessEqual(result["total_cost_microusd"], job.max_cost_microusd)
                self.assertLessEqual(result["total_predicted_ms"], job.deadline_ms + 1e-9)
                state = ledger.snapshot()
                self.assertLessEqual(state["spent_microusd"], state["limit_microusd"])
                self.assertEqual(state["reserved_microusd"], 0)
                for attempt in result["attempts"]:
                    selected = attempt["decision"]["selected"]
                    if selected["location"] == "remote":
                        self.assertEqual(job.privacy, "remote_allowed")
                        self.assertTrue(job.allow_remote)


class BudgetTests(unittest.TestCase):
    def test_atomic_concurrent_reservation_limit(self):
        ledger = BudgetLedger(100)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda i: ledger.reserve(str(i), 10), range(100)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(ledger.snapshot()["available_microusd"], 0)

    def test_atomic_duplicate_reservations(self):
        ledger = BudgetLedger(100)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: ledger.reserve("same", 10), range(100)))
        self.assertEqual(sum(results), 1)

    def test_settle_and_duplicate_settlement(self):
        ledger = BudgetLedger(100)
        self.assertTrue(ledger.reserve("a", 70))
        ledger.settle("a", 50)
        self.assertEqual(ledger.snapshot(), {"limit_microusd": 100, "spent_microusd": 50,
                                            "reserved_microusd": 0, "available_microusd": 50})
        with self.assertRaises(ValueError):
            ledger.settle("a", 50)
        self.assertFalse(ledger.reserve("a", 0))

    def test_over_settlement_preserves_reservation(self):
        ledger = BudgetLedger(100)
        ledger.reserve("a", 70)
        before = ledger.snapshot()
        with self.assertRaises(ValueError):
            ledger.settle("a", 71)
        self.assertEqual(ledger.snapshot(), before)

    def test_settlement_does_not_release_other_reservations(self):
        ledger = BudgetLedger(100)
        ledger.reserve("a", 50)
        ledger.reserve("b", 50)
        ledger.settle("a", 0)
        self.assertEqual(ledger.snapshot()["reserved_microusd"], 50)

    def test_stale_quote_cannot_exceed_budget(self):
        ledger = BudgetLedger(100)
        self.assertTrue(ledger.reserve("first", 80))
        self.assertFalse(ledger.reserve("stale-second", 80))

    def test_invalid_money_is_rejected(self):
        for value in (-1, 0.1, True, float("nan")):
            with self.assertRaises(ValueError):
                BudgetLedger(value)


if __name__ == "__main__":
    unittest.main()
