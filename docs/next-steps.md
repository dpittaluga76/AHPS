# AHPS implementation status and task history

Updated November 6, 2026. Current CLI usage is described in the
[repository README](../README.md), with research navigation in the
[documentation index](index.md).

## Current implemented features

| Feature | Status | Boundary |
| --- | --- | --- |
| Ordinary AHPS | Binary transform search, v5 archives, optional native acceleration | Existing preset/search defaults are documented in [search methods](search-methods.md) |
| CSV column grouping | `compress` and `bench` accept `--csv`, with exact v6 reconstruction | One measured CSV input, supported comma/double-quote dialect, smaller-archive selection. [Results](../reports/csv-compression.md) |
| Tensor-aware encoding | `compress` and `bench` accept `--safetensors [SOURCE]` and `--source-offset N` | v7, exact F32 and FP8 representations, maximum 32 MiB input. Larger models need externally extracted slices. [Results](../reports/safetensors-aware.md) |
| Terminal progress | Automatic on terminal stderr, or controlled with `--progress` and `--no-progress` | Completed stages determine percentage. [Validation](../reports/terminal-progress.md) |
| AHPS-only benchmark | `python ahps.py bench INPUT --ahps-only` | Encodes, decodes, and verifies AHPS. The default benchmark also compares gzip and XZ |

The CSV and tensor candidates compete with the ordinary AHPS archive at the
same resolved preset. Both features are opt-in and require the updated decoder
when their new container wins. The [format contract](format.md) documents
compatibility, buffering, and decoder limits.

## Remaining work

Whole-model streaming for multi-gigabyte safetensors inputs, broader CSV and
tensor evaluation, independent platform validation, and the frozen final
evaluation remain open. The rejected decoder-memory change remains a recorded
experiment. The relational and independent-backend codecs remain research
implementations. Existing development findings do not establish general
compression performance across file types.

## Recorded November 3 task order

The tasks below preserve the original sequence and its completion criteria.
Execution instructions refer to that historical study. Local corpus files,
frozen binaries, and raw records may be absent from a public checkout.

Tasks 1 through 5, 7 and 8 were completed. Further task-4 backend work was skipped
after its feasibility stop. Task 6 was deferred on the measured evidence.
Task 8's decoder candidate missed its acceptance targets and was not retained.
See the [POLY2 validation report](../reports/poly2-dedup-validation.md),
[profiling results](../reports/profiling.md), [backend pilot report](../reports/backend-experiment.md),
[standalone-SPLIT report](../reports/standalone-split.md), and
[decoder-memory outcome](../reports/decoder-memory.md).

This queue puts correctness and measurement before new candidate discovery. The saved backend pilot comes before standalone-SPLIT changes so its representation remains fixed. The [format contract](format.md), [backend protocol](representation-backend-experiment.md), and [development and holdout rules](../reports/evaluation-protocol.md) govern the work.

## Historical ordered tasks

### 1. Freeze the development reference

- [x] Record the reference commit, native-library hash and ABI, build settings, Python/liblzma identities, explicit search method, and codec settings.
- [x] Declare development inputs and comparison cases. Preserve reference plans, container bytes, archive hashes, sizes, and verified restored bytes before changing the scorer.
- [x] Use the same inputs and settings for each comparison. Keep sealed evaluation files and reserves outside this queue.

Completed: the original source/DLL and 29 reference archives are preserved with their plans, containers, inputs, hashes, and settings. The evidence records the exact Python backend binary because its liblzma version string is not exposed.

### 2. Remove the duplicate POLY2 trial

- [x] Keep only the canonical `a = d2_0 >> 1` candidate in both `ahps_simd.c` and the Python scorer in `ahps.py`.
- [x] Compare each engine's scoring with its frozen reference, retain cross-engine regression coverage, and compare baseline/candidate archive bytes on the declared development cases.
- [x] Measure scorer calls and total encoding time separately; report the observations without assuming a whole-encoder speedup.

The discarded candidate differs by `128*i*(i-1)`, which is zero modulo 256. Its residual and score are identical, and strict tie handling retains the first candidate. Completion requires unchanged selected costs/parameters and archive bytes under the pinned comparison settings. Decoder semantics, format, and defaults remain unchanged.

Completed: 254 exact scoring comparisons per engine, 29 identical plans/containers/archives, and 220 passing regression tests. The candidate DLL is installed. Diagnostic timings establish no end-to-end speedup; attribution remains task 3.

### 3. Profile encoding and decoding

- [x] Separate native scoring, Python planning, transform work, and actual LZMA trials in encoding. Identify the measured shares of POLY2 and LCG screening before considering generator gating.
- [x] Measure exclusive decode time for XZ decoding, segment assembly/allocation, inverse transforms, scratch-buffer copies, checksums, and output writes. Within EXE_SPLIT, distinguish the preliminary scan from restoration.
- [x] Record allocation lifetimes and a working-set timeline covering Python, native scratch, and liblzma memory. Keep total Windows peak working set as the comparable process metric; phase peaks are not additive contributions.
- [x] Compare diagnostic instrumentation with clean controls using API durations and whole-process peak working set, and report the observed overhead.

Completion: an attribution report identifies measured costs and unresolved attribution. It does not presume that the EXE_SPLIT scan or Python copies explain the existing runtime and memory ratios. Profiling does not change the archive representation.

Completed: the [Blender profile](../reports/profiling.md) attributes 93.40% of encode time to LZMA trials and about 9.15% of decode time to the EXE_SPLIT scan. It identifies overlapping assembly buffers at the highest sampled working set, with scalar-only lifetime measurements and clean controls. Production files and archive bytes are unchanged. Findings remain limited to this development file.

### 4. Run the saved representation and backend pilot

- [x] Follow the [existing protocol](representation-backend-experiment.md), including its execution prerequisites, wrapper review, pinned PAQ8PX build, feasibility probe, controls, resource limits, and reporting rules.
- [x] Use the frozen reference planner from task 1. The POLY2 simplification may substitute only after the comparison cases establish identical plans and container bytes; record the exact build used.
- [x] Recreate the pilot containers within its timed per-input preparation. Task 1's reference artifacts do not replace that work or exempt it from the protocol's preparation, batch, and global limits.
- [x] Freeze each reached input's RAW and transformed container bytes before backend execution. Keep standalone-SPLIT discovery out of this pilot.

Completion: report every planned outcome, including infeasibility, failures, and inactive transforms. This scheduling update does not change the saved protocol's budgets or authorize its execution. An inconclusive pilot does not prevent the independent SPLIT experiment below.

Completed: the maintainer authorized execution on November 3. The original reference planned the fixed 256 KiB Blender probe during a 0.609-second preparation. All three PAQ probe cells round-tripped and duplicate B matched exactly. Their complete durations scaled to 628.744–633.744 seconds per 2 MiB cell, exceeding the saved 300-second limit; batch and global feasibility estimates also failed. The pilot stopped after 237.250 seconds. All 35 main cells are recorded as unstarted, and no backend/transfer gate passes. This is a completed feasibility outcome, not a completed matrix. See the [report](../reports/backend-experiment.md).

### 5. Measure standalone SPLIT on fixed typed regions

Executed: the [fixed-region protocol](standalone-split-experiment.md) pins the existing MR file's declared uint16 pixel region, 22 complete-file methods and three local windows. No further PAQ run is scheduled.

- [x] Declare development regions, segment boundaries, alignments, and strides `2, 4, 8, 16` before comparing sizes. Use known typed regions without adding an automatic detector yet.
- [x] Compare RAW, the existing delta candidate, standalone SPLIT, and delta-to-SPLIT with identical backend settings and full framing costs.
- [x] Measure complete solid-stream archive sizes as well as local trials, exact restoration, encoding/decoding time, and peak working set. Report regressions alongside gains.

Completion: determine whether standalone SPLIT adds useful value beyond existing candidates on these inputs. Keep selective-lane deltas and format changes outside this experiment. A new backend study using these changed representations would be a separate follow-up to task 4.

Completed: 22 complete archives and 63 local combinations round-tripped. Best standalone SPLIT was 2,582,552 bytes, 6.19% smaller than fixed RAW but 4.40% larger than the best existing DELTA1 → SPLIT chain (2,473,804 bytes). Automatic AHPS produced 2,481,576 bytes. Every local window favored standalone SPLIT, demonstrating a local/full-file ranking reversal. A console-helper monitoring fault interrupted the initial attempt; it is preserved and excluded from the clean series, which finished in 86.641 seconds under the original deadline. See the [results and limitations](../reports/standalone-split.md). Scope is one existing uint16 MR parent, with no float-tensor or broader-corpus claim.

### 6. Evaluate automatic SPLIT discovery

Deferred: the fixed MR experiment did not establish a standalone-SPLIT improvement beyond existing chains. No detector or discovery policy was changed. Other typed classes would need their own fixed-input evidence before revisiting this decision.

- [ ] Proceed if task 5 establishes useful gains. Expose standalone SPLIT independently of prior delta selection, then evaluate candidate discovery as a separate change.
- [ ] Compare automatically selected results with task 5's fixed-candidate results. Record missed opportunities, unhelpful selections, discovery cost, and complete-archive effects while retaining the RAW fallback.

Completion: establish the discovery method's benefit and cost before changing the default search policy. If task 5 finds no useful benefit, record that result and defer this task.

### 7. Select the next optimization from the evidence

- [x] Rank follow-ups using the profile and experiment reports: buffer reuse/copy reduction, separate LCG/POLY2 ablations, or further representation work.
- [x] Consider a stored EXE_SPLIT target count only if measured scan cost justifies a separate versioned experiment. Preserve validation and count all metadata costs.
- [x] Keep 7-Zip BCJ2 and typed-data codecs as appropriate external comparisons. Handle production default changes, broader corpus claims, and sealed evaluation separately.

Completion: choose one measured follow-up with an explicit reference, scope, and success criterion. Do not combine unrelated changes in one comparison.

Completed: select incremental assembly in the file decoder's `ContainerStream.read`. The [decision and experiment contract](decoder-memory-experiment.md) ranks alternatives, pins the current decoder and existing archives, preserves allocation/rejection behavior, and defines matched controls. The primary target is at least 8 MiB lower median Blender peak working set, with at most 5% median decode-time regression per input. This is a selected experiment, not a measured candidate improvement.

### 8. Implement and measure incremental file-decoder assembly

Execution authorized by the maintainer: implement the candidate and compare it with the frozen baseline. The original source/DLL were preserved before editing; acceptance remains the saved task-8 contract.

- [x] Preserve the pinned reference and change only streaming segment assembly, growing storage from bytes actually decoded.
- [x] Validate the new accumulation path's allocation bounds, malformed-input behavior and existing decoder regressions.
- [x] Run the fixed five-pair Blender/MR/RAW comparison under the [task-8 contract](decoder-memory-experiment.md), verify every restoration and report all outcomes.
- [x] Keep the production change only if the declared correctness, memory and time criteria pass; retain evidence either way.

Completion: one measured, reviewable decoder-memory change or a documented unsuccessful experiment. Encoder, native kernel, scratch policy and archive format are outside this candidate.

Completed: 224 regression tests and all 30 measured round trips passed. Blender median peak memory fell by 2.855 MiB against the required 8 MiB, and median API time rose 7.91% against the allowed 5%. MR/RAW controls passed. The candidate was rejected under the frozen criteria and the exact production source/test baseline restored. All candidate artifacts and measurements remain available in the [outcome report](../reports/decoder-memory.md). No automatic retuning or rerun followed the result.

## Execution boundaries

### New local research branch

The maintainer subsequently authorized `codex/relational-compression` from `main`
at `0dac8d2`. The [first relational experiment](relational-compression.md) tests
causal relationships in known record layouts in an independent research codec.
Its first milestone is a tested prototype and a bounded comparison on a declared
Blender development table plus synthetic controls. It does not reopen task 8,
change production AHPS, or authorize final/sealed evaluation.

First milestone completed: the independent prototype and 23 new tests are in
place; all 243 repository tests passed. The original cohort retained the real
table's 2 MiB-cap exclusion and 27 passing synthetic cases. A separate 3 MiB-cap
table-only follow-up passed all nine real round trips. Record-aware word delta
produced 511,972 bytes versus AHPS's 535,212 on the table, but adaptive prediction
selected that same delta model and added no cross-field gain. The declared
cross-field continuation criterion failed. See the [results and limits](../reports/relational-compression.md).

The maintainer then requested the exact `corpus_development` folder. A bounded
whole-file adapter and 18 tests were added without changing the AHRL core or
production AHPS. All 261 tests passed. Four complete-file comparisons finished:
AUTO selected RAW and was 6.86%–15.45% larger than AHPS. Node completed XZ/RAW,
then hit its shared 300-second worker deadline during AUTO, leaving its AHPS
case unstarted. The below-normal, serial run retains 18 successful round trips
and both missing results; no retry or production adoption followed. See the
[corpus report](../reports/relational-corpus.md).

The requested [same-boundary table comparison](same-boundary-table-experiment.md)
is complete: 106 targeted tests, 42 archive round trips and 30 residual checks
passed. Word delta beat the best tested existing control on four of the five
development-folder tables and the reused Blender reference; Chrome favored byte
delta. Automatic AHPS matched the best fixed D1(12,0) -> SPLIT(12) archive exactly
on every table. The residual-only probes preserve the same win/loss pattern,
supporting a conditional word-arithmetic candidate. No production/core changes
were made. See the [full results](../reports/same-boundary-tables.md).

The maintainer authorized that complete-file hybrid next. It is now implemented in
an independent AHRH1 research codec, retaining original AHPS as the exact
fallback. The [frozen hybrid protocol](selective-word-hybrid-experiment.md)
compares original AHPS with byte/word table programs at matched boundaries and
executable origins, each in one solid XZ stream. All 136 targeted tests,
15 measured encodes and 20 output round trips passed. All five selected files
became smaller, saving 22,828 bytes overall (0.090%); four selected word delta
and Chrome selected byte delta. Word delta adds 3,836 bytes beyond selecting
the smaller of AHPS and the aligned existing control. See the
[results and search-cost limits](../reports/relational-hybrid.md).

Keep the hybrid on the research branch. Production adoption, broader independent
evaluation and a separate speed/memory assessment remain future decisions;
no extra tuning or comparison was started from these results.

The maintainer then authorized an independent backend without LZMA2 and requested
a different branch. `codex/independent-compression-backend` was created from
committed checkpoint `3788ed9`. The new AHRC1 C backend implements LZ77 matching,
adaptive probability models and binary arithmetic coding; its candidate paths
do not call the old planner or another compressor, and its fallback stores
bytes in AHRC rather than returning an AHPS archive. Production code is unchanged.

The [independent-backend experiment](independent-backend-experiment.md) completed
135 targeted tests, 25 encode operations and 30 verified output round trips.
All five selected archives are larger than frozen AHPS, totaling 6,165,295 extra
bytes (24.32%). The independent codec is a working starting point, not a size
improvement; the earlier LZMA2-backed result remains separate. See the
[full results and independence checks](../reports/independent-backend.md).
Keep this implementation and negative comparison on its own branch. Any further
parser/model change requires a separate frozen experiment; none was started
after these results.

Tasks 1 through 5, the task-7 selection and task 8 were completed at the user's request. Further task-4 work was skipped; task 6 is deferred. The rejected task-8 candidate was preserved in isolation and production restored. Performance measurements run without competing benchmark workers. Each experiment retains its own resource limits; decoder work does not extend the backend pilot.
