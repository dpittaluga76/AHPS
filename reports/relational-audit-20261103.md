# Independent relational-experiment audit

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Reviewer: `relational_tests`. The evidence audit passes;
the cross-field continuation criterion does not. This review read and hashed
existing artifacts without running any encoder, decoder or benchmark.

## Preserved cohorts and correctness

The first cohort (local record `relational-first-20261103.json`) retains its original status:
27 successful synthetic round trips out of 36 scheduled cells, with nine real
table cells unavailable under the 2 MiB cap. Its report hash is unchanged.
No result has been replaced or retrospectively marked successful.

The complete Blender exception table is 2,484,204 bytes, above that initial cap.
The separate [follow-up protocol](../docs/relational-table-followup.md) raised
only table eligibility to 3 MiB before any real-table compression result existed.
The table-only cohort (local record `relational-table-20261103.json`) then completed nine of
nine round trips. These are **36 actual successful decodes across two cohorts**,
not a repaired 36-of-36 first experiment.

All 36 actual archive and restored-file identities were rehashed. Every restored
length and SHA-256 matches its frozen input. All four measured input snapshots,
both freezes, source/test snapshots, runtime binaries and Job records match
their recorded identities. The real table is byte-identical to the declared
region of the fully rehashed Blender parent: offset 109,946,368, length
2,484,204, comprising 207,017 three-uint32 records.

The research codec and test source are identical across both cohorts. Production
Python, C and DLL remain the accepted baseline. The preserved test log verifies
243 tests passing in 20.063 seconds with no skips; this includes 23 new tests
covering independent wire vectors, modular arithmetic, causal references,
malformed archives and bounded decompression.

## Attribution and acceptance

| Input | Best non-cross control, bytes | Best cross/adaptive, bytes | Interpretation |
| --- | ---: | ---: | --- |
| Planted exact | XZ: 196,680 | Adaptive: 65,832 | Mechanism works on the constructed relationship |
| Planted noisy | XZ: 196,680 | Adaptive: 67,048 | Mechanism tolerates the specified constructed noise |
| Independent fields | XZ: 196,680 | Cross: 196,756 | No gain; framing costs remain |
| Real exception table | Delta: 511,972 | Adaptive: 511,972 | No cross-field gain |

For the real table, delta, independent, adaptive and auto archives are
byte-identical. All three adaptive descriptors are previous-value predictors;
none uses a nonzero-coefficient same-record affine prediction. Auto selects
delta in the prescribed tie order. Therefore both the required 1% improvement
over the strongest non-cross control and the active cross-field attribution
condition fail. Gate calculations were recomputed independently.

The real table's baseline AHPS archive is 535,212 bytes. The 511,972-byte result
is 4.34% smaller, attributable here to full-width integer delta plus byte shuffle.
It is a region result, not a whole-executable saving or evidence of a new
cross-field breakthrough. Synthetic savings cannot satisfy the real-data gate.

## Resource and measurement checks

The fixed method schedules and all unstarted slots are retained. All four
measured worker trees ran serially, were assigned to Jobs before resuming,
denied breakaway, used the 2 GiB aggregate commit cap, exited successfully
within their 120-second worker and 600-second batch deadlines, and were cleaned
up. API encode/decode timings are single observations; shared worker state and
Job-level memory metrics do not establish stable speed or per-codec memory
comparisons. No experiment was rerun during this audit.

## Audited identities

| Artifact | SHA-256 |
| --- | --- |
| First report | `4922723f6b16f7a6d3adae4c25a956c89cab9323796441c8bbf99efa1f4efcfa` |
| Table-only report | `696ece54a658b72506f93830a8648dc2fbccaf44bffe8ac589e331b369af38b9` |
| Research codec, both cohorts | `6c65a1517a0c10e25261cf9eb242ab9d3e52b35f7948d31a5cd915fa53dcc041` |
| Research tests, both cohorts | `5aaa4975201feec13cc154b5745023b584e95e74ad0c18406d2fa3fe4f117203` |
| `experiments/relational-preflight-20261103/tests.log` | `908c8a07320621ea22b4654aa87ee36ea5f1b52587970001532cce02c0b220fb` |
