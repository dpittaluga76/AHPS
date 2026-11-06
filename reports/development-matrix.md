# Current development corpus batch

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

The maintainer requested the existing test corpus and a short run. This replaces the earlier broad experiment proposal.

- Inputs: `dickens`, `mozilla`, `mr`, `nci`, `reymont`, `samba`, `sao`, `webster`, `x-ray`.
- `ooffice`, `osdb` and `xml` remain sealed. Installed applications, Blender, reserves and predicate measurements are excluded from this batch.
- Compare default AHPS with 7-Zip BCJ2 once per file: 18 encode/decode pairs. Report complete archive sizes, percentage size differences, source/archive hashes and every round-trip result.
- This is a size/correctness smoke test on development data. Captured single-pass times and peaks are diagnostic records, not repeated-timing or product-readiness claims.
- Implementation is fixed at `4080e23ffe134b03266ece7f5c519ced4e53523d`; AHPS preset 9 extreme, block size 4096, ABI 206, all four features enabled. BCJ2 is 7-Zip 26.00 with `-mx=9 -md=64m -mf=BCJ2 -mmt=1 -ms=off`; retain the actual method listing.
- Maximum 300 seconds per phase and a 900-second total watchdog. The watchdog stops the owned benchmark process tree if necessary. Record incomplete/missing pairs; do not retry or silently extend the run.
- File order is the list above, with AHPS then BCJ2 per file. Keep main and its artifacts unchanged while running. Announce the runner in root `xchange.txt`; no other benchmark, compilation or heavy test competes with it.

The explicit initial 18-job plan is `corpus-smoke-plan.json`. The output is `corpus-smoke-20261031.jsonl`, with stdout, stderr and a budget-status JSON alongside it.

After that baseline completed, four follow-ups were announced and performed within the ORIGINAL deadline: MR, SAO and X-ray with `delta_split=false`, and Samba with `per_chunk=false`. They reused the unchanged default controls, source hashes and implementation. The complete 22-job plan is `corpus-components-plan.json`; follow-up output is `corpus-components-20261031.jsonl`. All 22 pairs finished in 421.2 seconds from the original start. The size-only report includes every planned pair.

The remaining component removals, RAW/XZ and BCJ baselines, and interaction comparisons remain later development work. Their scope and cost will be recorded separately; this batch does not complete the component/interaction gate or authorize final holdout evaluation. See `corpus-results-overview.md` for the two useful delta-to-SPLIT findings and why the RAW-winning SAO/Samba comparisons leave their corresponding component contribution unresolved.

For the later transform-only predicate experiment, EXE32 under ABI 9206 must remain byte-identical to production. The ModRM hook changes only EXE_SPLIT. Whole-file residual XZ results cannot be compared directly with normal AHPS archives or earlier code-section-only measurements.
