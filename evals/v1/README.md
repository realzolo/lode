# Lode V1 Evaluation Corpus

The corpus is versioned, deterministic, and contains no production data.
`gold-incidents.jsonl` exercises report abstention and evidence authority.
`security/native-reads.jsonl` is the policy oracle smoke corpus.
`security/malicious-evidence.jsonl` exercises prompt/evidence injection handling.

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
