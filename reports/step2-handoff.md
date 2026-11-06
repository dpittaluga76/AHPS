# Step 2 checkpoint after the bounded development batches

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Newer checkpoint: [Step 3 Blender search comparison](step3-handoff.md). The text below records the earlier Step 2 state.

Reviewed core `5ac59a3` and prepared harness/test work `6af4997` were integrated through local main commit `4080e23`. Nothing has been pushed. The benchmark reviewer retains serialized main integration; root `xchange.txt` is the coordination file.

Validation completed:

- All 131 integration tests passed, including the real feature API, enabled-only scoring, native/Python interoperability and independently frozen default archive bytes.
- Bounded fuzz passed 1,000 cases in each of five campaigns at the new core.
- Both unsafe experimental build configurations failed compilation; ABI 9206 was built from the reviewed source and refused by the normal archive bridge.
- Experimental and production transforms round-tripped 2,000 synthetic cases each. EXE32 was unchanged in every case; EXE_SPLIT differed in 1,162. Matching inverse and XZ framing checks passed.
- The report generator passed 16 synthetic tests and validated the complete current 22-job size-only evidence.

The short test-corpus batch is complete: 22 successful pairs in 421.2 seconds from its original start, under one 900-second budget, with no retries or holdout reads. See `corpus-results-overview.md` for interpretation and `corpus-results.md` for the validated detailed table. Single-pass times and peaks are retained as diagnostics only.

The strongest direct component evidence is the delta-to-SPLIT benefit on MR and X-ray. Samba and SAO selected RAW in both compared configurations, so their equal sizes do not establish that a component is generally unnecessary. Six of nine default winners used RAW, including NCI; none used EXE32 or EXE_SPLIT.

The maintainer subsequently chose Blender only for the x86 follow-up, as relayed by the core reviewer in `xchange.txt`. All five new pairs passed in 331.2 seconds under a separate 900-second ceiling. Disabling EXE_SPLIT increased the complete archive by 85,912 bytes (0.326%) against the historical default that the core reviewer reproduced with the new core. Removing the ModRM predicate increased whole-file EXE_SPLIT residual-XZ by 114,884 bytes (0.414%); EXE32 was byte-identical. See `blender-components-overview.md` for the explicit cross-commit caveat and separate experiment table. These effects are not additive, and no new timing or memory comparison is claimed.

The maintainer also approved one Blender `per_chunk=false` follow-up. It passed in 153.2 seconds, producing 27,770,972 bytes with one whole-file EXE_SPLIT segment: 5.270% larger than default and 1.755% larger than historical BCJ2. Code, DLL and runtime hashes matched the preceding removal. This supplies an informative chunking comparison on Blender, with the same cross-commit size-only caveat; the earlier Samba result remains uninformative. Across both development datasets, all 28 new pairs passed, with no retries or held-out inputs.

Outstanding work is explicit: the remaining removals/interactions and RAW/BCJ controls, broader applicability of the observed component benefits, search-cost experiments, freeze and final holdouts are not complete. There are no queued runs. Keep final candidates/reserves and sealed Silesia inputs unopened. Do not reuse older approximate or concurrent results as current controlled measurements.
