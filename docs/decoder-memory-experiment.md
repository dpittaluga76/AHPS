# Next optimization: incremental file-decoder assembly

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026. Task 7 selected reducing memory in `ContainerStream.read`.
Task 8 subsequently implemented and measured the candidate: all correctness
checks passed, but Blender saved only 2.855 MiB and its median decode time rose
7.91%, missing both predefined targets. The production baseline was restored.
See the [outcome](../reports/decoder-memory.md). The original experiment contract
below and its frozen copy retain the criteria used before measurement.

## Decision and ranking

The [profile](../reports/profiling.md) identifies a concrete memory overlap:
15 MiB of decoded source pieces, a new 15 MiB destination and retained 15 MiB
native scratch coexist at the sampled peak. The clean Blender file decoder's
median peak working set was 138.934 MiB. These are logical buffer capacities
and an observed process peak, respectively; they must not be added together.

| Priority | Follow-up | Evidence and decision |
| --- | --- | --- |
| 1 | Incremental file-decoder assembly | Directly targets the retained source-piece list while preserving the compressed representation. Select this bounded memory experiment. |
| 2 | Reduce encoder LZMA trial cost | Actual LZMA calls consume 93.40% of profiled encoding; two final whole-file trials consume 80.05%. Potentially substantial, but skipping/reranking trials needs separate archive-size controls and RAW fallback protection. |
| 3 | EXE_SPLIT target-count experiment | The preliminary scan is 9.15% of profiled decode time. Removing all of it would be about a 1.10x idealized throughput improvement. A stored count needs versioned framing, validation and byte-cost accounting; defer until a separate experiment justifies that cost. |
| 4 | Typed-boundary/representation work | On MR, a fixed existing delta-to-SPLIT chain beats automatic AHPS by 7,772 bytes (0.31%). Standalone SPLIT loses to that chain, and short-window rankings reverse on the full file. More typed inputs and whole-archive checks are needed before a discovery change. |
| 5 | Scratch-copy acceleration | Scratch-to-buffer copy is 0.29% of profiled decoding. A small measured time target; keep separate from the memory experiment. |
| 6 | Separate LCG/POLY2 gating ablations | Combined generator screening accounts for approximately 0.112% of Blender encoding. The profile supplies little encode-speed motivation for prioritizing it. |

The [PAQ pilot](../reports/backend-experiment.md) ended at feasibility, and the
owner skipped further backend execution. It established no backend winner.
The [SPLIT screen](../reports/standalone-split.md) concerns one existing uint16
development file. Neither result changes the decoder's format contract.

This ranking prioritizes a bounded memory improvement that preserves archive
bytes. It does not claim that memory assembly is the largest time component.
Assembly accounted for 3.22% of profiled decoding; XZ decoding accounted for
70.14%. A segment's worth of logical overlap is an opportunity, not a promised
15 MiB reduction in resident memory. Buffer growth, reallocation and allocator
retention may reduce or erase the benefit.

## Single-change implementation scope

Currently `ContainerStream.read(count)` first collects all requested decoded
pieces with `take(count)`, then allocates and fills another contiguous buffer
through `assemble`. For a transformed segment this retains its entire source
piece list when the destination is allocated.

The candidate will accumulate the destination as bounded decoded pieces arrive,
releasing each consumed piece before requesting more. It must return the same
exact-length mutable buffer or reject an incomplete section. Preserve bytes
that belong to the next section in the stream's queue. `pull` remains the maintainer
of XZ decoding, header checks and decoded-byte accounting.

Restrict the comparison to the streaming assembly path and directly necessary
helper code. Keep shared `assemble`, the buffered API, legacy v3 buffering,
native scratch lifetime/copies, C source/DLL, encoder, search, opcode semantics
and archive layout outside this change. A transformed segment and native
scratch still need contiguous storage. Do not combine this experiment with
scratch release across RAW segments or an EXE_SPLIT redesign.

**Allocation invariant:** a header/table may declare a large segment, but it
must never cause `bytearray(count)` or equivalent full allocation before those
bytes actually decode. Growth must depend on received data, with bounded
per-piece slack. Scratch remains allocated only after a complete segment is
available. Review reallocation peaks as well as retained references.

Preserve existing dictionary/container/output limits, early declared-size
rejection, bounded compressed reads and RAW writes, exact section coverage,
supported v3/v4/v5 behavior, XZ end/checksum validation, restored CRC32, and
temporary-file cleanup/atomic output replacement. A rejected archive must leave
an existing output untouched. The [format contract](format.md) governs these
requirements.

## Reference and fixed inputs

Use the current verified POLY2-deduplicated decoder as the reference, matching
the clean task-3 snapshot. These identities were verified during task 7:

| Reference artifact | SHA-256 |
| --- | --- |
| `ahps.py` | `c7dcc554c64f4abf78fef6852603e91de9db4048ae5ffe81c4ff007b61eead2d` |
| `ahps_simd.c` | `4e340be401057d53aa897cb3af67b557f233031c2339542deea971c841fbfb16` |
| `ahps_simd.dll`, ABI 206 | `71df88e8d41c9f233838c6563de714427ccc46782407d20836c0bc1e937c6022` |
| `docs/format.md` | `78a5740858b21a91d226e53183c73f3a7b086ed08cc54df57296d125cbafacce` |

Before candidate edits, preserve an isolated reference copy. Freeze candidate,
runner and test identities before timing. Reuse these existing archives; do not
re-encode corpus files merely to measure a decoder:

| Role | Archive | Archive SHA-256 | Expected restored SHA-256 |
| --- | --- | --- | --- |
| Primary memory target | `experiments/poly2-dedup-20261103/artifacts/candidate/blender-full-native.ahps` (26,380,608 bytes) | `975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd` | `9d5df7a9ac2a7c5085aa0669700021bfa5c5e92de167a2510b0d0942c34deab0` |
| Structured-data control | `experiments/split-20261103/cases-r2/existing-ahps/archive.ahps` (2,481,576 bytes) | `5ee96ccdcef90dd3abc49158c0d9ced2d45ecabc4da67c568e29001d6ee5f1af` | `68637ed52e3e4860174ed2dc0840ac77d5f1a60abbcb13770d5754e3774d53e6` |
| RAW-path control | Deterministic synthetic 16 MiB: `bytes(range(256)) * 65536`, stored as one zero-step v5 segment using preset 9 EXTREME | Freeze once during preparation | Freeze once during preparation |

The synthetic case tests a code path; it is not corpus evidence. Keep all
development/holdout guards in effect. No sealed file or reserve is needed.

## Correctness checks before timing

Run the existing file-decoder, decode-limit, legacy/golden-archive and core
regressions with native and Python coverage where supported. Add focused tests
only for behavior affected by streaming assembly: piece boundaries, pieces
spanning adjacent sections, truncated sections, declared-large table/segment
lengths backed by tiny payloads, and output preservation on failure.

The existing allocation test in `test_decompress_file.py` spies on `assemble`.
If the new streaming path no longer calls that function, this test alone is
insufficient: the buffered decoder also calls it during the paired rejection
check. Observe the new streaming accumulation path directly to demonstrate
bounded allocation and no premature native scratch. Keep valid/rejection parity
with the reference, without weakening malformed-input checks to fit the change.

## Matched measurement and acceptance

After correctness passes, run Blender, MR, then the RAW fixture, one worker at a
time under the benchmark lock. For each input run five reference/candidate pairs
in fresh processes, alternating order: R/C, C/R, R/C, C/R, R/C. Use the pinned
Python/liblzma runtime from profiling and the native DLL above. Use no cache
flush; record this policy. Both implementations decode identical archives into
fresh output paths through `ahps_decompress_file`.

Measure file-API wall time including close/flush and atomic replacement; record
launch-to-exit time separately. Capture the actual Python worker's kernel peak
working set before output verification, then verify exact restored length/hash.
Keep helper-process and aggregate-commit accounting distinct. Use the corrected,
validated task-5 Job runner, which waits for complete process-tree exit under
the deadline rather than mistaking a retiring console helper for codec failure.

Use a 30-second ceiling per decode and 600 seconds for the measured batch, with
an 8 GiB aggregate Job commit limit. Preserve every attempt and report failures.
Do not keep rerunning noisy observations until a threshold passes. These are
new small decoder-only experiment bounds, not an extension of the PAQ budget.

Acceptance for this development experiment requires all of the following:

1. All correctness tests and 30 measured round trips pass. Archive bytes/hashes,
   native DLL and format remain identical to their frozen references.
2. Blender's median candidate peak working set is at least **8 MiB lower** than
   the matched reference median. This is a chosen minimum useful improvement,
   not a measured result or a guaranteed allocator outcome.
3. Median file-API decode time is at most **5% higher** on each of the three
   fixed inputs. Publish all five paired observations and ranges; an unresolved
   timing/environment concern is inconclusive, not a stable speed claim.
4. For MR and RAW, median peak working set does not increase by more than
   `max(2 MiB, 5% of that input's reference median)`.

Historical 138.934 MiB and 1.165905 seconds provide context only; acceptance uses
the new matched controls. Report a missed threshold as such. Do not change
opcodes, search, dictionary size or acceptance criteria to rescue the result.
If accepted, the production diff should contain only this assembly change and
its relevant tests/docs. Otherwise retain the experiment and its evidence.

7-Zip BCJ2 remains the executable external comparison, and typed-data codecs
remain separate comparators for representation work. This decoder experiment
does not rerun them or establish new historical speed/memory ratios, compression
ratio improvements, broader corpus acceptance, or sealed-evaluation claims.
