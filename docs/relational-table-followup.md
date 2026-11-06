# Relational codec: table-only eligibility follow-up

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026, same local research branch. This is a separate input-preparation
follow-up to [the first protocol](relational-compression.md), declared before
any compression measurement on the real table.

The first cohort correctly marked Blender's exception table unavailable under
its 2 MiB cap. Header-only diagnosis found a present, aligned table of 2,484,204
bytes (207,017 three-uint32 records), 387,052 bytes above that cap. No real-table
compression result informed this change. All 27 available synthetic cases passed;
the first cohort's continuation criterion remains unmet. Preserve its report,
all source snapshots and attempt records without modification.

This follow-up permits the same complete table up to **3 MiB**. The codec's
8 MiB limit, models, predictor fitting, source data, nine comparison methods,
backend preset, success criterion and runtime/resource limits are unchanged.
No alternative file or region is substituted. The real table is not a new
independent application and cannot establish whole-file or corpus-wide gains.

Use explicit harness profile `table-only`, with a separate immutable directory
`experiments/relational-table-20261103` and report
`reports/relational-table-20261103.json`. Freeze the updated harness and this
document alongside the unchanged codec, test source and production baseline.
Keep the original first-cohort source snapshots for historical reproducibility;
their live harness file will differ after this documented profile addition.

Verify the same complete Blender parent hash before and after extraction and
retain exactly the directory's declared 2,484,204 bytes if all PE bounds checks
pass. The fixed order remains raw, shuffle, delta, independent, cross, adaptive,
auto, XZ, AHPS. All framing/model costs count. Require nine exact round trips
and at least 1% smaller complete size for the best cross/adaptive method than
the best non-cross control, with a nonzero-a same-record predictor actually used.
This is a size-screen continuation gate, not a production performance gate.

Use the same shared lock, one serial worker, 120-second worker limit, 600-second
batch deadline and 2 GiB aggregate committed-memory cap. Keep all diagnostics
and failures; single-pass API times do not establish a stable speed ratio.
Do not repeat synthetic cases or change any model based on their measured result.
Do not retry or tune the real comparison to pass. A positive result warrants
another independent development input; it does not authorize production adoption.
