# POSM AI Booster

**Version 0.1.0 | November 3, 2026 | Simulation only**

A local-first GPU job-routing prototype with deterministic tests and synthetic examples.
Safe local logging and rule-based diagnostics are implemented. Actual LLM, GPU,
cloud, and on-demand service integrations are not connected.
It is **not** a GPU driver, a remote GPU service, a measured performance result,
or an AWS deployment. It cannot make an existing application's graphics calls
use another machine's GPU. It routes descriptions of explicit compute jobs.

Nothing here opens a network connection, sends a payload, reads credentials,
starts an instance, installs a driver or package, changes the system, or spends
money. Names such as `local-simulated` and `aws-gpu-simulated` are fixtures,
not discovered machines. Every price and performance value is invented.

## Run

Requires an existing Python 3.10+ installation. No external dependencies.

Open a terminal in the project root (the folder containing this README), and run:

```sh
python -m gpu_router
python -m unittest discover -s tests -v
python -m gpu_router --json
python -m gpu_router --output my-demo-report.json
```

On systems where Python is named `python3`, use `python3` instead. The output
option refuses to overwrite an existing file. A generated report is included
at `examples/demo-report.json`.

## Implemented

- Immutable job and worker descriptions with explicit numeric validation.
- Local-only and zero-spend defaults. Remote routing requires **both** a
  `remote_allowed` policy and `allow_remote=True`.
- Hard eligibility checks for privacy, healthy and fresh telemetry, supported
  workload/model, memory, predicted deadline, job budget and session budget.
- A deterministic end-to-end estimate: queue + transfer + RTT + startup +
  compute, plus configurable uncertainty padding.
- Local preference unless a feasible remote route saves at least 100 ms **and**
  20% by default. At the exact threshold, the remote route qualifies. It must
  be strictly faster even when both thresholds are zero.
- Fastest eligible remote route, then lowest cost, then worker ID for stable
  ties. If no local worker is eligible, an authorized feasible remote route
  can be selected without a local-comparison threshold.
- Rejection reasons for every ineligible worker. All-rejected means no route.
- Integer microdollar budgets and thread-safe, atomic reservations. A changed
  budget or duplicate attempt prevents a simulated dispatch.
- A failure simulation: at most one local fallback after a remote failure.
  The fallback must fit the **remaining** original time and job budget and
  re-check health, freshness, capabilities and session budget. It never retries
  remotely or silently relaxes privacy.

## Safe logging and diagnostic extension

Both features are off by default. To opt in locally:

```sh
python -m gpu_router --log-jsonl safe-events.jsonl
python -m gpu_router --diagnostics
python -m gpu_router --json --diagnostics --log-jsonl safe-events.jsonl
```

The JSONL output refuses to overwrite an existing file. Its version-1 allowlist
contains only `schema_version`, `synthetic`, a per-batch `sequence` number,
`status`, `attempt_count`, `predicted_ms`, and `cost_microusd`. Numeric fields
have strict finite ranges; status is a fixed enum. Unknown input fields and
all job/worker/model identifiers, customer paths, prompts, payloads, secrets,
raw errors, reasons, and nested attempt data are dropped, not hashed. Invalid
required values fail closed with fixed errors that do not echo those values.
No log file is created unless requested. The CLI writes only to the requested
local file and configures no external logging handlers or automatic uploads.
Caller-owned streams in the Python API must be trusted local destinations.

**The existing full `--json` / `--output` demo reports are not sanitized logs.**
They contain synthetic fixture names and detailed decisions. Do not reuse full
report serialization for private workloads. Only the JSONL allowlist is the
safe telemetry path; redaction cannot protect data deliberately encoded in
otherwise valid numerical fields. Telemetry remains local and should still be
reviewed before sharing.

`--diagnostics` runs deterministic local rules over those sanitized events and
returns advisory codes such as `review_rejections` and `review_fallbacks`.
It does not diagnose root causes or demonstrate improved performance.
**No AI model is connected, no AI debugging has been performed, and no learned
optimization is implemented.** The `DiagnosticProvider` Python protocol is a
typed extension point for a future separately reviewed integration, not a
runtime plugin loader. The shipped provider and output source are explicitly
`local_rules_no_ai`. Disabled diagnostics never invoke a provider. Provider exceptions become a
fixed `diagnostic_unavailable` advisory without recording exception text or a
traceback. Invalid returned schemas are rejected.

The diagnostic output schema allows no commands, URLs, free-form model text,
route changes or budget edits. The application only displays validated
advisories after routing has completed. Providers receive no router, ledger,
raw report or payload reference. In-process Python providers must be trusted:
this interface is not a sandbox against malicious custom code. Connecting a
real model requires separate security/data-sharing review, a source-schema
update, and explicit authorization before transmitting telemetry. Do not add
credentials to code or logs.

## Example scenarios

The demo includes private/local processing, an opted-in fast remote, a large
upload that makes local faster, a zero-budget job, unavailable memory capacity,
and remote failure followed by permitted local fallback. These demonstrate
decisions, **not actual speedups, measured latency or provider charges**.

## Prediction and accounting definitions

- Latency and deadlines are milliseconds. A deadline is the total allowed
  duration from the start of the simulated job, not a calendar time.
- Telemetry timestamps and `now` are seconds on the same virtual clock.
  An age equal to the 30-second TTL is allowed; older or future samples fail.
- Payload size is decimal MB. Bandwidth is decimal Mbps. Transfer time is
  `8 * MB / Mbps * 1000` milliseconds, plus one configured RTT. Local jobs
  have no modeled network transfer.
- Compute time is `work_units / units_per_second * 1000`. Both must refer to
  the same workload/model profile; this is not a hardware-independent measure.
- Memory is a caller-supplied GB estimate, including whatever model/runtime
  overhead the caller expects. Equality with worker capacity is allowed.
- Padding defaults to 10% of the complete latency estimate. It is an invented
  safety margin, **not a calibrated confidence interval or p95 guarantee**.
- Money uses integer micro-USD: 1 USD = 1,000,000 micro-USD. The conservative
  fixture estimate prices the whole padded duration plus output egress and
  request fees, then rounds up to a whole microdollar. This does **not** model
  any actual AWS billing policy, minimum instance charge, tax or discount.
- Equality at deadline and budget limits is allowed. Estimates above limits
  are rejected. Reservations are atomically rechecked after route planning.
- A failed simulated attempt consumes its full estimated time and cost. There
  is no assumed refund. Duplicate settlement is rejected without mutation.
- A session is one `BudgetLedger` object in one Python process. It has no
  durable storage or multi-process coordination. Restarting creates a new
  ledger. **Do not use it as a production billing guardrail.**
- Actual remote spending cannot be hard-capped by a point estimate alone.
  Future execution needs enforceable provider limits, duration/cancellation
  controls, reserves for billing uncertainty, and reconciled actual charges.
- Accepted numeric magnitudes are capped for predictable arithmetic.
  Positive rates and bandwidths must be at least `1e-9` and at most `1e12`.

## Code layout

```text
gpu_router/router.py      Models, prediction, constraints, selection, ledger,
                          and deliberately non-executing simulation
gpu_router/__main__.py    Six synthetic scenarios and report CLI
gpu_router/diagnostics.py Allowlisted events and opt-in local advisory interface
tests/test_router.py      Unit, boundary, concurrency and fallback tests
tests/test_diagnostics.py Logging, redaction, opt-in and advisory boundary tests
examples/demo-report.json Full deterministic example output
ARCHITECTURE.md           Real integration plan and explicit safety gates
```

## AI Booster and local-model-first direction (design only)

**AI Booster** is the proposed user-facing, on-demand compute service: a user
submits an explicit supported job, sees the privacy/cost constraints, and gets
a job result. It is not a transparent Windows graphics driver and does not
redirect arbitrary application GPU calls. There is no launched service here.

For future AI diagnostics, the intended preference is the user's verified local
LLM first, if one is separately verified, authorized and available. No local
model, endpoint, running server or credentials are assumed or detected by this
prototype. Local-model integration and any cloud adapter are design/contract
only. If local inference is unavailable, retain deterministic local rules or
report that diagnostics are unavailable. **Never silently fall back to cloud.**
A cloud diagnostic requires explicit approval covering destination, sanitized
data scope and any cost under the applicable policy, separately from a job's
remote-compute flag. See ARCHITECTURE.md for the proposed adapter and metrics.

## What is not implemented

No GPU execution, inference server, Windows/Linux driver, CUDA interception,
transport protocol, worker authentication, distributed queue, real telemetry,
durable billing, cross-process budget enforcement, learned AI routing, ad
rewards, payments, external service endpoint or deployment.

The next useful step is an authorized **local real-workload benchmark** and a
small, separately approved LAN worker. Remote/cloud data handling and a scoped
spending limit must be agreed before AWS resources or billable tests begin.

## Validation

See `VALIDATION.md` for commands and results from the tested artifact.

## License

MIT License. Copyright (c) 2026 POSM Software. See [LICENSE](LICENSE).
