# Bounded search-cost experiments

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Current product policy is documented in [search defaults](../docs/search-methods.md): automatic Candidate A only at resolved preset 9 + EXTREME. The harness continues to pass explicit search settings, with omitted `--search` or `{}` meaning exhaustive. The protocol and measurements below describe the earlier experiment design.

Prepared 2026-11-01 from the consolidated plan and subsequent maintainer decisions
relayed by the core reviewer in root `xchange.txt`. This document concerns development only.
The final evaluation rules in `evaluation-protocol.md` remain a separate draft.

## Current scope and ownership

- Inputs are only `blender`, `dickens`, `mozilla`, `mr`, `nci`, `reymont`,
  `samba`, `sao`, `webster` and `x-ray`, selected explicitly from the development
  manifest. Installed-application development inputs are excluded this step.
- Each announced batch has a **900-second overall ceiling**, including all its
  jobs. This newer owner limit overrides the older development document's
  900-second-per-phase policy. Phase limits cannot extend the overall deadline.
- The core reviewer owns the core selection implementation; the benchmark reviewer owns harnesses, tests,
  manifests and reports. Separate worktrees and exact-commit cross-review remain
  required. The benchmark reviewer integrates main; root `xchange.txt` coordinates the runner.
- One runner at a time. No concurrent builds, tests or compression jobs. Keep
  main and the measured worktree fixed during each batch.
- No sealed input or reserve may be opened, hashed or measured. The generator
  research path remains unchanged. No final evaluation or acceptance claim is
  authorized by this step.

## Diagnostic first batch

The core reviewer runs one instrumented default Blender encode from the reviewed baseline.
Preserve wrapper/source/DLL/input identities, exact settings, archive size/hash,
per-chunk candidate IDs and sizes, winner choices, and stage/trial durations.
Distinguish exclusive from nested times and separate final container trials
from chunk trials. The expected default archive is 26,380,608 bytes with SHA-256
`975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd`.

Instrumentation overhead prevents using this run as a performance baseline.
Use its attribution to choose a small, explicit hypothesis. Neither per-chunk
planning nor any other stage is assumed to dominate before the measurements.
Cheap low-preset trials previously mis-ranked whole-file Blender candidates;
their usefulness per chunk is a hypothesis to test, not an established shortcut.

## Agreed candidate interface

The two-field API and three-way pilot were agreed in root `xchange.txt` before
parallel edits. Reviewed core `6b06c13` adds a frozen
`SearchMethod(chunk_trial_preset=None, exe32_chunk_rival=True)` and
`ahps_compress_bytes(..., search=EXHAUSTIVE)`. `CompressionReport.search`
confirms the requested method. Feature toggles remain unchanged. The trial cap
uses compression levels and preserves the final preset's EXTREME flag when no
lower cap applies. The rival setting affects only the extra whole-chunk EXE32
candidate, not scoring, refinement or a planner-produced EXE32 plan. Search
changes apply only when the existing large-file chunked path is selected.
The harness must not label a run with a method the worker did not receive and confirm.
The default/exhaustive reference and candidate must be selectable in one frozen
implementation. Keep feature settings, native backend, preset, block size,
runtime and input identities fixed except for the declared selection method.
For the first cheap-trial comparison, preserve the final compression preset
and whole-file RAW fallback, and identify exactly which trial calls use the
cheaper preset. Removing that fallback is a separate hypothesis.

Search may select existing programs differently. Decoder semantics, format v5,
and native semantic identity stay fixed. Any incompatible experimental encoding
requires its own explicit version and may not masquerade as a normal archive.

## Finite comparisons

1. The explicit `step3-blender-pilot-plan.json` declares three Blender cases:
   exhaustive; cheap trials only (`chunk_trial_preset=1`, rival retained); and
   Candidate A (the same cap, extra EXE32 rival removed). The pilot runs each
   once at final preset 9 extreme for archive size and exact round trip.
   Its elapsed times are diagnostic only. It does not automatically authorize
   a larger matrix or a new candidate sweep.
2. If pursuing timing, freeze the methods and all artifacts, declare three
   repetitions per method, and run one complete pair per announced batch.
   Predeclare alternating order: reference/candidate, candidate/reference,
   reference/candidate. If BCJ2 is included, predeclare its order too and run it
   fresh in the same timing cohort. Keep stable repetition IDs 1, 2 and 3 across batches.
   Invocation count must not reset those IDs or misstate the intended cohort.
3. Use `benchmark_ahps.py`'s fresh encode and separate decode processes, existing
   root lock, kernel peak working set, source-hash checks and pinned identities.
   Record total process time and retain operation time only as a separate metric.
   No cache flush; decoding follows encoding, consistently with the baseline.
4. Record every attempted job and any unstarted planned jobs. A timeout or
   failed round trip is reported, with no automatic retry. Stop the owned process
   tree at the overall deadline; preserve partial evidence and failure status.

Additional same-corpus checks must be explicitly selected and announced within
their own finite batch; no automatic all-files expansion. Do not combine the
historical Blender baseline or the instrumented profile with the new paired
timing cohort. Size-only historical comparisons, if needed, retain the existing
explicit cross-commit caveat outside the same-commit report matrix.

## Reporting and decisions

Add the agreed selection setting to expected-job and observed-group identity,
worker confirmation and display labels. Preserve rejection of mixed commits,
artifacts, source identities, duplicate repetitions, missing jobs and failures.
Legacy rows without a selection field retain their documented original method.
An incomplete three-repetition cohort cannot produce a validated timing claim.
The current reporter requires BCJ2: introduce an explicit selection-comparison
mode whose required reference is exhaustive AHPS instead. Preserve the existing
BCJ2 mode and validation unchanged. Feature-removal comparisons must stay within
one selection method. Scheduling metadata belongs outside compressor settings;
record a stable comparison ID, intended repetitions and actual execution order.

Report final complete archive bytes and hashes, exact round trips, all timings
and peaks, medians and ranges for the completed timing cohort.
Repeated runs of each fixed method/input must produce equal archive sizes.
Treat size variation as an unresolved reproducibility failure; do not average
it away or select the best repetition. Retain every archive hash as evidence.

For each input:

- Size cost: candidate bytes minus exhaustive bytes, and that difference divided
  by exhaustive bytes.
- Encoding time saved: one minus the candidate/reference ratio of median process
  encoding times. Keep decoder time and peak-memory ratios separate.
- Report candidate choice differences where available, including cases where a
  cheaper search loses the useful chunk choices established on Blender.

Publish regressions alongside gains. A smaller search does not by itself show
lower decoder memory. No product default changes or final freeze follow merely
from one favorable development result; the measured tradeoff remains reviewable.
