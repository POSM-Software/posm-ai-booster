"""Run with python -m gpu_router. All inputs and outputs are synthetic."""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from .router import BudgetLedger, Job, Router, Worker, run_simulated
from .diagnostics import diagnose, sanitize_result, write_jsonl


def demo() -> dict:
    now = 1_000.0  # Fixed virtual time, not a system clock or live measurement.
    workers = (
        Worker("local-simulated", "local", ("inference",), ("demo-model",),
               12, 10, now),
        Worker("lan-gpu-simulated", "remote", ("inference",), ("demo-model",),
               24, 80, now, rtt_ms=4, upload_mbps=900, download_mbps=900,
               compute_microusd_per_second=200),
        Worker("aws-gpu-simulated", "remote", ("inference",), ("demo-model",),
               48, 200, now, rtt_ms=45, upload_mbps=100, download_mbps=100,
               startup_ms=100, compute_microusd_per_second=600,
               egress_microusd_per_mb=100, request_microusd=10),
    )
    router = Router(workers)
    base = Job("private-inference", "inference", "demo-model", 100, 8,
               input_mb=1, output_mb=0.1, deadline_ms=20_000)
    jobs = [base,
            replace(base, id="authorized-remote", privacy="remote_allowed", allow_remote=True,
                    max_cost_microusd=10_000),
            replace(base, id="transfer-heavy", input_mb=3_000,
                    privacy="remote_allowed", allow_remote=True, max_cost_microusd=10_000),
            replace(base, id="budget-limited", privacy="remote_allowed", allow_remote=True),
            replace(base, id="no-feasible-worker", memory_gb=100),
            replace(base, id="remote-failure-local-fallback", privacy="remote_allowed", allow_remote=True,
                    max_cost_microusd=10_000)]
    ledger = BudgetLedger(15_000)
    results = []
    for job in jobs:
        failure = frozenset({"aws-gpu-simulated", "lan-gpu-simulated"}) if "failure" in job.id else frozenset()
        results.append({"job_id": job.id, **run_simulated(router, job, now=now,
                                                        ledger=ledger, fail_workers=failure)})
    return {"prototype": "offline-gpu-job-router", "version": "0.1.0",
            "synthetic": True,
            "notice": "Predictions from invented fixtures; no measured GPU results, network calls, cloud resources, or actual spending.",
            "units": {"latency": "milliseconds", "payload": "decimal MB",
                      "bandwidth": "decimal Mbps", "cost": "integer micro-USD"},
            "results": results, "ledger": ledger.snapshot()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline synthetic GPU job-routing demo")
    parser.add_argument("--json", action="store_true", help="Print the full machine-readable report")
    parser.add_argument("--output", type=Path, help="Write the full report to a new JSON file")
    parser.add_argument("--log-jsonl", type=Path,
                        help="Write allowlisted synthetic events to a new local JSONL file")
    parser.add_argument("--diagnostics", action="store_true",
                        help="Opt in to local rule-based advisories (no AI model connected)")
    args = parser.parse_args()
    report = demo()
    if args.log_jsonl or args.diagnostics:
        events = tuple(sanitize_result(result, sequence=i)
                       for i, result in enumerate(report["results"]))
        if args.log_jsonl:
            with args.log_jsonl.open("x", encoding="utf-8") as output:
                write_jsonl(events, output)
        if args.diagnostics:
            report["diagnostics"] = asdict(diagnose(events, enabled=True))
    if args.output:
        # Refuse to overwrite existing files by accident.
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print("OFFLINE SIMULATION ONLY: invented telemetry/pricing, no GPU execution or spending.")
        for item in report["results"]:
            route = " -> ".join(a["decision"]["selected"]["worker_id"] for a in item["attempts"]) or "none"
            print(f"{item['job_id']}: {item['status']} | {route} | "
                  f"predicted {item['total_predicted_ms']:.1f} ms | "
                  f"simulated ${item['total_cost_microusd'] / 1_000_000:.6f}")
        print(f"Simulated session spend: ${report['ledger']['spent_microusd'] / 1_000_000:.6f}")
        if args.diagnostics:
            print("Local advisory only (no AI model): " +
                  ", ".join(report["diagnostics"]["codes"]))
        if args.output:
            print(f"Report written: {args.output}")


if __name__ == "__main__":
    main()
