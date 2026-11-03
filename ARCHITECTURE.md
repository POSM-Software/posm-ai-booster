# Architecture and next integration steps

## Current boundary

```text
Synthetic job description + synthetic worker inventory
                         |
          Hard policy/capability/health filters
                         |
      End-to-end latency and conservative cost estimates
                         |
       Deadline/budget filters and local-first selection
                         |
               Atomic simulated reservation
                         |
      Deterministic simulated outcome + decision reasons
```

There is no executor or network boundary in the implementation. Because it
does not handle payloads, an accidental `remote_allowed` flag cannot expose
data in this prototype. A real system will need much stronger authorization
and data-governance boundaries than a trusted boolean in a Python object.

## Phase 1: real local measurements

Select one explicit job type, for example one approved inference model with
synthetic prompts. Verify the local runtime and GPU capabilities through
authorized access. Record repeatable warmed and cold latency, model version,
device type, input/output sizes, memory peak and run outcome. Preserve a
CPU/local fallback. Do not make broad hardware claims from one workload.

Use a supported application adapter or SDK. An ordinary kernel graphics
driver is a different project with operating-system and graphics API concerns.
The first deliverable should submit explicit jobs and receive results rather
than promise transparent acceleration for every application.

## Phase 2: one approved worker

Use a known, authorized machine and a bounded workload. Add authenticated,
encrypted transport; identity-verified worker inventory; application-enforced
privacy classification; strict request schemas and allowlisted model/operation
names; deadlines/cancellation; idempotency; output validation and size limits;
and isolated execution. Do not send arbitrary commands or serialized code.

Separate the control plane from payload transfer. Keep payloads and secrets
out of decision logs. Treat worker telemetry as untrusted, check freshness,
detect implausible values, and maintain health backoff/circuit breakers. A
worker claim of "local" or "trusted" must not itself grant that status.

Repeated concurrent jobs also require explicit capacity reservations and queue
coordination. The current queue estimate does not reserve GPU capacity.
Continuous probes must stay within an approved monitoring and cost scope.

## Phase 3: approved AWS pilot

AWS remains a possible worker destination, not configured access. Before any
resource, data transfer or billable run, establish the exact account/region,
service/instance type, allowed payload class, total spend cap, stop conditions,
data retention, and who may authorize the action. Creating credentials or
expanding persistent access needs separate approval. No AWS keys belong in
source files, logs, reports or this ZIP.

Read current provider documentation and prices at integration time. Quote
startup, idle and execution duration, storage, data transfer, minimum billing
increments and relevant fees. Add durable, atomic accounting shared by all
dispatchers and reconcile actual charges. Exclude pending discounts and
unverified credits from spendable budget. Include cleanup and verified
shutdown as part of the pilot, not an optional follow-on.

## Phase 4: learned optimization

Start with the explainable baseline implemented here. Collect authorized
measurements with model, workload size, transfer sizes, queue, cold/warm state,
hardware and route. Fit a latency predictor to compute/network/startup
components, or compare a learned candidate ranking against the baseline.

Evaluate on held-out time windows with p50/p95 latency, prediction error,
deadline misses, failed attempts, total actual cost and privacy-policy
violations. Do not train on or upload private payloads merely to optimize
routing. First run a predictor in shadow mode, without changing real routes.

Learned scores may only rank candidates that already passed deterministic
authorization, privacy, memory, freshness, health and budget checks. Never let
a model override those checks. Require a fallback to baseline on drift,
missing features or uncertainty. Exploration needs a separate bounded cost
and privacy policy. Lower predicted latency is not evidence of lower actual
latency until measured.

## Optional ad rewards: concept only

One possible product model is opt-in rewarded ads subsidizing eligible compute.
This requires a compliant ad-provider integration, explicit consent where
needed, fraud controls, verified settlement and clear user-visible economics.
No ad provider, advertiser funding or revenue is established here.

Only settled, withdrawable credit should reduce an authorized compute charge.
Pending ad views are not money and must never bypass the budget limiter.
Do not collect extra personal data or sell workload content to fund compute.
If expected ad income is below cloud cost, default to local work or ask for
an explicit paid budget. No real ads, payments or credit ledger are included.

## Implemented logging and diagnostic boundary

The optional local path is one-way:

```text
Completed synthetic result
    -> explicit allowlist projection (drop identifiers and all nested data)
    -> immutable versioned SafeEvent, strict validation
    -> requested local JSONL file
    -> explicit diagnostic opt-in -> trusted LocalDiagnostics
    -> validated closed Advisory codes -> display only
```

Logging and diagnostics are independently opt-in. No raw object is passed to
providers, and no provider result flows back into routing or accounting. The
only shipped diagnostic implementation is deterministic local rules; there is
no AI model, model endpoint, credentials, network connector or command runner.
The typed DiagnosticProvider protocol is a future extension seam, not evidence
that an AI integration exists or that debugging/optimization has been done.

Both input and output are revalidated at the boundary, including instances
created by custom providers. Unknown raw input fields are ignored; malformed
required fields are rejected without copying their contents into errors. A
bounded immutable batch is validated in full before logging begins. No raw
exception capture, arbitrary messages, persistent identifiers, timestamps,
customer paths, prompts, payloads or secrets are supported by the schema.
Only the allowlisted JSONL path has this guarantee; the legacy full report is
an unsanitized synthetic demonstration and is not a production logging API.

Trusted Python extensions can execute arbitrary Python independently of this
interface, so the protocol is not an isolation boundary. Any future AI adapter
requires a separately authorized destination and data scope, security review,
bounded retention, updated source schema, and isolation appropriate to its
trust level. Model outputs must remain untrusted advisories and cannot override
privacy/eligibility gates, select routes, run commands or mutate budgets. Real
AI debugging, root-cause verification and optimization remain unimplemented.

## Proposed AI Booster surface and local-model-first adapter

AI Booster is a proposed on-demand explicit-job service, not an installed
service, driver, CUDA interceptor or transparent Windows acceleration layer.
A future UI/SDK should submit a supported job with explicit privacy and cost
constraints, then expose status, a result and safe diagnostics. Its execution
plane and diagnostic plane need separate authorization and budget scopes.

The planned diagnostic adapter contract, not an implemented connection:

1. Input: only a bounded tuple of validated SafeEvent records. Never the raw
   job, prompt, payload, customer path, worker identity or full report. Treat
   the telemetry as data, never model instructions.
2. Preference: a separately verified and authorized local LLM on the user-owned machine.
   A local endpoint label is not proof of locality: verify endpoint ownership,
   binding and absence of remote forwarding before trusting it. Do not discover,
   install, start or assume a model/server as a side effect of diagnosis.
3. Invocation: explicit diagnostic opt-in, bounded tokens, timeout and resource
   limit; no tools, shell, file access, routing/ledger handles or write authority.
   A future adapter must translate model text to the closed advisory schema and
   validate it. Free text is not a log event or an executable instruction.
4. Failure: use the shipped deterministic local rules or a fixed unavailable
   advisory. Provider exceptions in the present implementation already become
   `diagnostic_unavailable`; exception messages and tracebacks are not emitted.
   Malformed outputs are rejected. No retry may implicitly invoke a cloud model.
5. Optional cloud: only after explicit approval of the provider/destination,
   data fields, purpose and applicable spend. Remote-compute approval on a job
   does not authorize uploading logs to an AI provider. No cloud adapter exists.
6. Output: validated advisory only. A future source enum may distinguish verified
   local-model and approved-cloud outputs; today's schema deliberately accepts
   only `local_rules_no_ai`. Do not relabel synthetic rules as model output.

Proposed evaluation metrics (not measured or persisted in this version):
local-model availability, diagnostic duration and timeout rate, schema rejection
rate, advisory correctness on synthetic fixtures with known causes, false alarms,
resource use, and separately approved model-call cost. Collect aggregate counters
or coarse histograms without prompts, output text, stable identifiers or paths.
A future schema revision must explicitly allowlist any additional fields.
Compare local rules and a candidate local model in offline/shadow mode first.
Measure routing p50/p95 latency and actual job costs separately; faster diagnostic
responses do not establish faster GPU jobs or effective AI routing optimization.
