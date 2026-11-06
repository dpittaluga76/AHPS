# Step 3 checkpoint: Blender search comparison

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Current policy: after the corpus/default checks below, the maintainer approved Candidate A as the automatic default **only for resolved preset 9 + EXTREME**. Other presets retain exhaustive search. See [search defaults and benchmark usage](../docs/search-methods.md). The benchmark harness continues to select exhaustive explicitly unless given another search setting. The earlier results and decisions below remain a historical record.

Earlier checkpoint for the [remaining development corpus](step3-development-a-overview.md). Preset 9 showed no size losses, but a direct preset-3 Blender comparison failed the general no-loss condition. General promotion was rejected. The subsequent preset-9-only default is stated above, and the text below records the earlier Blender checkpoint.

The first bounded search-cost comparison is complete. See [the results overview](step3-search-results-overview.md) and [the validated three-repetition table](step3-blender-timing-results.md). This extends the [Step 2 checkpoint](step2-handoff.md); it does not complete the broader component matrix, freeze, or independent evaluation.

All measured work used reviewed commit `58b80588a6dbdb21dbd839caab89322c85dde827`, already integrated into local main by `db38b86d691c2b34eaf8a35b55caeecb100bc00a`. Their Git trees were identical before this report-only change. No remote push was made. Root `xchange.txt` remains the coordination file, with the benchmark reviewer serializing integration and the core reviewer reviewing his work.

Implementation and validation:

- The core reviewer's corrected core adds an opt-in `SearchMethod`; the default remains exhaustive. Search settings affect chunk-plan trials only. Final preset, RAW fallback, decoder, v5 format and native ABI 206 are unchanged.
- The benchmark reviewer's harness records requested and confirmed search settings, comparison identity, global repetition IDs and execution order. The reporter requires explicit, matching exhaustive references and complete planned repetitions.
- All 175 integration tests passed, including 12 direct search-method tests. Existing corpus and Blender evidence still validate with the default reporting mode.
- The separate size pilot passed all three pairs. Cheap trials alone increased the archive by 76,444 bytes. Candidate A, which also omits the extra whole-chunk EXE32 rival, reproduced the exhaustive archive exactly.
- Nine fresh timing pairs passed: three repetitions each of exhaustive, Candidate A and BCJ2. No failures, retries or unstarted jobs occurred. Each trio stayed below its independent 900-second ceiling, with 300-second phase limits and alternating codec order. Pilot observations are excluded from timing statistics.

Candidate A's median encode time was 86.901 seconds versus exhaustive's 180.958 seconds, a 51.98% reduction. Both produced the same 26,380,608-byte archive and SHA-256 on every timing repetition. Against fresh BCJ2, Candidate A was 3.34% smaller, took 3.257 times the encode time and used 4.923 times the decoding peak working set. These are results for one previously used development executable; they do not establish the proposed final x86 research or product targets.

On this file, the encoding ratio is still just above the working 3x target and decoding memory exceeds the working 2x target. Quote this cohort's 26.680-second BCJ2 median for this comparison; the historical roughly 28.7-second result is from a different cohort and is not pooled here.

Exact original JSONL, plans, budgets, supervisor scripts, integration log and run logs were preserved outside Git before line-ending normalization. The provenance manifest (local record `step3-evidence-provenance.json`) records all 37 preserved files and hashes, measured implementation/runtime identities and batch outcomes. The checked-in JSONL rows were not rewritten.

No default change, RAW-trial skipping, further benchmark or implementation work is queued. Decoder memory remains unresolved. Broader component comparisons and independent evidence are still needed before freeze and final evaluation. Sealed inputs and reserves remain unopened and unhashed. Stop here for the maintainer's choice of the next bounded task.
