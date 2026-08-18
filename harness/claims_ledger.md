# Claims ledger

Each row a claim the paper may make, the measured evidence that supports it, and its
verification status. A claim is **VERIFIED** only when backed by committed `results/`
records from a real run AND a `verify:false` pricing snapshot. No claim is pre-marked
verified.

| # | Claim | Evidence source | Status |
|---|-------|-----------------|--------|
| 1 | Durable per-workflow cost = mean ± 95% CI | `results/durable-*-r*.json` → `analysis.aggregate` | PENDING full run |
| 2 | Step Functions Standard per-workflow cost = mean ± 95% CI | `results/sfn_standard-*-r*.json` | PENDING full run |
| 3 | Durable is N% cheaper than SFN Standard at volume V | ratio of #1/#2 per volume | PENDING full run |
| 4 | SFN state-transition cost is the dominant SFN cost component | `state_transitions.gross` × snapshot price vs other components | PENDING full run |
| 5 | Durable incurs ZERO state-transition cost | absence of SFN in durable arm (structural) | STRUCTURAL — true by architecture |
| 6 | Free-tier changes the SFN cost story at low volume | gross vs net columns in `summary.csv` | PENDING full run |
| 7 | Lambda GB-seconds computed from real per-function memory (1024 durable / 512 sfn, 256 sfn-approval) | `functions[].memory_mb` live from Lambda API | METHOD READY |
| 8 | Express excluded (5-min cap + no waitForTaskToken) | design decision; no Express arm exists | DOCUMENTED |
| 9 | Prices sourced from dated snapshot, region us-east-1 | `pricing/pricing_2026-08-12.json` | PENDING HP price verification |

## Pricing verification checklist (HP)
- [ ] lambda.arm64.request_per_million
- [ ] lambda.arm64.gb_second
- [ ] step_functions_standard.state_transition_per_million
- [ ] dynamodb.write_request_unit_per_million
- [ ] dynamodb.read_request_unit_per_million
- [ ] s3.put_per_1000
- [ ] s3.get_per_1000
- [ ] sns.publish_per_million
