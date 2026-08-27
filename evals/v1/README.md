# Lode V1 Evaluation Corpus

The corpus is versioned, deterministic, and contains no production data.
`gold-incidents.jsonl` exercises report abstention and evidence authority.
`security/native-reads.jsonl` is the policy oracle smoke corpus.
`security/malicious-evidence.jsonl` exercises prompt/evidence injection handling.
`operational-cases.jsonl` freezes identity, Connector-selection, and model-routing
cases used with both security corpora by the complete operational gate.

Case IDs are stable and unique. Corpus records contain expected classifications,
not hidden model reasoning. Release evaluation output must freeze provider
account class, deployment revision, role, execution class, prompt, schema,
parser, policy, and corpus hashes. Training or prompt-tuning data must live
outside `evals/v1` and may not reuse release cases.

Real-provider release observations may repeat each frozen case. Every JSONL row
must have a globally unique `observation_id`, the stable `case_id`, actual result
state, cause/causal/evidence/version/counter-evidence booleans, and positive
evidence references. The observation set must cover every frozen case; repeated
confirmed runs provide the sample size needed for the Wilson lower bound. Runs
must not invent new case IDs or omit abstention cases.

Candidate and canary baseline run manifests contain non-empty strings for
`run_id`, provider account class, model deployment and revision, role,
execution class, prompt/schema/policy revisions, and the SHA-256 of
`gold-incidents.jsonl`.

Operational observations cover every case in `operational-cases.jsonl` and both
security JSONL files. Identity rows report `verified` and `correct`; security
rows report `actual`; Connector rows report `selected`, `useful`, `cost`, and
`latency_ms`; routing rows report `actual_execution_class`,
`compression_correct`, `tokens`, `cost`, and `latency_ms`. Observation IDs are
globally unique.

The operational baseline is a strict `lode-operational-baseline.v1` object. It
binds the complete evaluation-corpus SHA-256 and freezes provider, deployment,
model revision, prompt, schema, and policy metadata. Its `metrics` object
contains every name returned by
`lode.application.release_evaluation.required_baseline_metrics`; minimum-rate
metrics cannot decrease and maximum rate/cost/latency metrics cannot increase.

The strict `lode-release-bundle.v1` object contains a non-empty `release_id`, the
gold and complete evaluation corpus hashes, and the SHA-256 of all six supplied
candidate, operational, and canary artifacts. `make provider-release-check`
verifies every binding before evaluating results. Synthetic fixtures test this
mechanism but never constitute release evidence; release requires frozen
observations from real configured providers and the deployment canary/shadow
run.
