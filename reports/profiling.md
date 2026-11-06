# AHPS encoding and decoding profile

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Task 3 is complete. These are diagnostic measurements on the frozen 113,041,880-byte Blender development input after POLY2 deduplication.

LZMA trials dominate encoding. EXE_SPLIT is the main inverse-transform cost, but its preliminary scan accounts for about 9% of total decoding time. Scratch-buffer copying is about 0.3%. The highest sampled working set occurs while assembly holds decoded source pieces and a new segment buffer alongside retained native scratch.

## Controls and verification

The production Python source, C source, and DLL were unchanged throughout profiling. An isolated copy used nested exclusive Python timers and a C build with QueryPerformanceCounter counters. Native phase counters subdivide the corresponding Python native-call timer; they must not be added to it.

One clean/profiled encode pair used explicit block size 4096, preset 9 + EXTREME, and `FAST_CHUNKS`. Both produced the frozen **26,380,608-byte archive**, SHA-256 `975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd`, and restored the original bytes. File decoding used three clean and three profiled fresh processes in alternating order, all with matching restored hashes. The native instrumentation also passed small scorer, EXE_SPLIT, reset, and timing-partition checks.

| Measurement | Clean | Profiled |
| --- | ---: | ---: |
| Encode API, one run | 86.246 s | 86.731 s |
| Encode process peak working set | 1,002.668 MiB | 1,002.719 MiB |
| Decode file API, median | 1.165905 s | 1.183418 s |
| Decode API range | 1.162049–1.168899 s | 1.183298–1.184002 s |
| Decode process peak working set, median | 138.934 MiB | 139.316 MiB |

The observed profile/control time differences are 0.56% for the single encode pair and 1.50% for median decode time. They support using these profiles diagnostically, not a precise instrumentation-overhead model. API durations exclude process launch; encoding also excludes the initial input read. File decoding includes closing/flushing and atomic replacement. Peaks were captured before output verification. There was no new BCJ2 run, so the earlier 2.35x time and 1.76x memory ratios remain historical context rather than an attribution result from this cohort.

## Encoding

| Exclusive work in the profiled run | Time | Share of encode API |
| --- | ---: | ---: |
| Two final whole-file LZMA trials | 69.430 s | 80.05% |
| 24 chunk-ranking LZMA trials | 10.760 s | 12.41% |
| 496 refinement LZMA trials | 0.818 s | 0.94% |
| Native scoring | 4.610 s | 5.31% |
| All other work | 1.114 s | 1.28% |

Actual LZMA calls account for **81.007 s, or 93.40%**. The existing `CompressionReport.lift_sec` and `lzma_sec` fields do not provide this separation: their scopes mix trial compression, transforms, and container construction.

Within native scoring, 27,599 blocks took approximately 3.505 s in strided delta/modal trials, 0.788 s in executable trials, and 0.220 s in raw estimation. POLY2 solving plus its 14,127 trials took **0.08848 s**. LCG screening plus 14 full-block trials took **0.00823 s**. Together those generator stages were approximately **0.112% of the complete encode**. This file therefore provides little end-to-end speed motivation for prioritizing generator gating. Trial counts do not establish how often a generator was ultimately selected.

## Decoding

| Measured component, median | Time | Share of profiled decode API |
| --- | ---: | ---: |
| XZ backend decoding | 830.104 ms | 70.14% |
| EXE_SPLIT inverse, including both passes | 216.743 ms | 18.31% |
| Segment assembly | 38.083 ms | 3.22% |
| CRC calculation | 30.724 ms | 2.60% |
| File writes | 20.105 ms | 1.70% |
| Scratch-to-buffer copying | 3.403 ms | 0.29% |

The table shows the largest components, not a complete partition. Full exclusive timings are in the evidence. Medians of components need not sum to the median total.

Five EXE_SPLIT calls process five 15 MiB segments. Native counters put the preliminary scan at **108.282 ms** and restoration at **108.642 ms**. These are subdivisions of the inverse time above. If the scan alone could disappear with every other cost unchanged, the estimated saving would be **9.15% of decode time**, or about **1.10x throughput**. That is an idealized bound for removing this phase, not a measured optimization. A stored target count would still need validation, metadata accounting, and versioned format work.

## Memory lifetimes

The highest sampled working set in a representative corrected run was **139.277 MiB** immediately after allocating a 15 MiB assembly destination. At that event, 15 MiB of decoded source pieces remained live; the preceding restore event showed retained 15 MiB native scratch. Those three buffer capacities overlap: **45 MiB of directly observed staging and scratch storage**, alongside decoder state, queued fragments, and the Python/native runtime. The kernel peak for that run was 139.313 MiB, slightly above the sampled maximum.

The pinned runtime's preset-9 EXTREME LZMA2 properties specify a 64 MiB dictionary. This is a configured capacity, not a measurement of dictionary residency at every event. Logical buffer sizes, private committed memory, and resident working set are recorded separately; their peaks are not additive per-phase contributions.

The trace also shows scratch retained across later RAW segments and the previous RAW piece surviving until the next read completes. `assemble` releases its source list progressively, with the last local source piece surviving until function return. Deleting a reference does not guarantee an immediate working-set decrease.

The initial memory hooks passed `locals()` and could extend buffer lifetimes. The entire initial decode cohort was excluded and replaced with six fresh runs using scalar-only snapshots. Original results and the reason for exclusion remain in the evidence. The encode pair remains valid: those decoder memory hooks were inactive during timed encoding, and round-trip verification was outside measurement.

## What the profile supports next

1. For encoding speed, investigate LZMA trial cost before generator screening. Any change to the final RAW rival or search policy needs its own size-loss controls.
2. For decoder memory, test incremental segment assembly or buffer reuse while preserving the existing allocation limits and malformed-archive behavior. Avoiding a segment's worth of simultaneously retained source pieces is a more substantial memory target than accelerating the 3.4 ms scratch copy.
3. Keep known-count EXE_SPLIT as a bounded follow-up hypothesis: a roughly 9% time component is measurable, but does not explain most decoding cost.

These findings informed the [decoder-memory experiment](decoder-memory.md). Incremental assembly was implemented and measured, missed its saved memory/time targets, and was not retained in production. The backend pilot ended at feasibility and further execution was skipped; the standalone-SPLIT screen is complete. Outcomes are recorded in the [task queue](../docs/next-steps.md).

## Evidence

- Audited results and provenance (local record `profiling-20261103.json`): accepted/excluded run identities, exclusive timers, native counter scopes, runtime/build identities, representative memory timeline, and verification hashes.
- Local artifacts under `experiments/profile-20261103/` preserve the clean source/DLL, instrumented source/DLL, original and corrected profiling drivers, native build validation, run plan, and every raw result. They are ignored by Git; their hashes and locations are recorded in the evidence.
- `profile.py batch` ran the initial serial cohort. `prepare_scalar_memory.py` prepared the corrected hooks; `profile.py corrected-decode` reran the entire decode cohort. `summarize.py` verified completeness, byte hashes, counter partitions, timing reconciliation, source identities, and unchanged production files.

One development file and one encode pair do not establish general throughput, cross-platform behavior, or the causes of historical BCJ2 ratios. No sealed inputs were accessed, and no production optimization was applied.
