# Validation record

Tested November 3, 2026 using Python 3.12.14 on Linux in an isolated working
folder. No hardware, provider, network, driver or production integration test
was performed. Python 3.10/3.11 and Windows were not tested.

## Passed

```sh
python3 -m compileall -q gpu_router tests
python3 -m unittest discover -s tests -v
python3 -m gpu_router
python3 -m gpu_router --json
python3 -m gpu_router --diagnostics
```

- **64 tests passed**, including a fixed-seed sweep of 1,000 synthetic jobs.
- Privacy and explicit remote opt-in; workload/model and memory checks;
  health, stale and future telemetry; deadline, transfer and cold-start costs;
  local preference boundaries; stable route ties and reordered inventories.
- Job/session budgets, concurrent reservations, duplicate jobs, rejected
  double settlement, unchanged reservations after excess settlement, and
  remaining-budget/deadline checks on local fallback.
- Exact rational cost ceiling, independent of ambient Decimal precision;
  tiny nonzero variable charges; invalid/huge/non-finite inputs; invalid
  original and advanced simulation clocks.
- Six demo cases and JSON serialization. Repeated demo output is deterministic.
- 20 additional logging/diagnostic tests cover exact schema fields, removal
  of identifiers/secrets/paths/nested payloads, no stringification of extra
  objects, non-echoing validation errors, finite numeric/type bounds,
  disabled-provider non-invocation, explicit boolean opt-in, immutable event
  inputs, rejection of forged events/invalid batches, deterministic local
  advisories, closed output codes, invalid-provider-output rejection, secret-bearing provider-exception
  containment without stderr output, static
  import/call inspection of the diagnostic module, unchanged routes/accounting,
  CLI default-off behavior and refusal to overwrite JSONL files. Static
  inspection is a regression guard, not a sandbox or formal security proof.
- Source contains only standard-library dependencies. No network/GPU/cloud
  execution code or credentials are included.

An additional independent review exercised 1,000 concurrent unique submissions
and 1,000 duplicate submissions. The unique run accepted nine cost-110 jobs
within a 1,000-unit budget (spent 990); the duplicate run charged only once.
The review identified arithmetic and validation edge cases, which were fixed
and added to the passing regression suite.

The earlier prototype ZIP was extracted into a fresh temporary directory. All 64
tests, compile checks, deterministic demo output, opt-in JSONL/diagnostic CLI,
and its bundled SHA-256 manifest were verified against those extracted bytes.
The source release adds project branding and the MIT license; the earlier ZIP
has not been rebuilt to include those release changes. The final source tree
was separately checked with the same 64 tests, compile checks, demo output,
and its updated SHA-256 manifest.

## Not established

Real local or remote GPU execution, real latency/throughput, AWS prices or
account access, remote payload privacy/authentication, actual billing limits,
production concurrency, Windows packaging, learned optimization effectiveness,
or ad economics. The included report is not a benchmark.

No actual AI model or provider was used. Local rule-based advisory checks are
not AI debugging, learned optimization, or a root-cause diagnosis. Malicious
in-process custom providers, production telemetry and network adapters were
not implemented or tested.
