# Selective word-delta whole-file hybrid

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026. Owner-authorized local research on
`codex/relational-compression`, checkpoint `e16f3b2`. The design follows the
[same-boundary evidence](../reports/same-boundary-tables.md) and the
[production format contract](format.md). Freeze this protocol, sources, tests,
runtime and exact inputs before the first measurement. Do not modify production
AHPS Python/C/DLL, existing AHRL codecs, earlier harnesses or saved evidence.

## Question and inputs

Do optional uint32 deltas on known exception-table records improve complete
executable archives after boundary changes, metadata and solid-stream effects?
Use every byte of exactly the five `corpus_development` files, in order:
`7zip-cli.exe`, `chrome.exe`, `git-cli.exe`, `node.exe`, `vlc.exe`. No Blender
rerun, slices, other files, sealed evaluation, downloads or online research.
Verify complete parent sizes/SHA-256 against the development manifest, snapshot
them and verify again. Retain unsupported/missing tables as explicit outcomes.

Use the existing validated x64 PE exception-directory extractor, with its entire
declared table, file offset, RVA and length. Maximum table size is 8 MiB, and
length must be a positive multiple of 12. The fixed layout is three little-endian
uint32 fields. No heuristic scan, alternate region, padding or layout search.
Sources are capped at 128 MiB. These are reused development files, not holdouts.

## Three encodes and one selection

1. `ahps`: call unchanged `ahps_compress_bytes` once, native ABI 206, block 4096,
   preset 6, explicit `FAST_CHUNKS`, default features. Preserve the exact winning
   archive. Recover its actual selected plan from the validated v5 container;
   do not reimplement or rerun the planner. Compare the four previously completed
   baseline archive hashes with the earlier frozen corpus report; Node previously
   had no completed AHPS result.
2. `aligned_byte`: preserve baseline segments outside the complete table, split
   crossing segments at its endpoints and replace the table with one existing
   DELTA1(12,0) -> SPLIT(12) segment. Encode with the research framing below.
3. `aligned_word`: use exactly the same outside plan and boundaries, replacing
   only that table's first transform with uint32 word delta. Same framing and
   final SPLIT(12), same full-file preset 6 CRC64 XZ.
4. `selective_hybrid`: choose the smallest complete archive among the three in
   that order, with strict improvement required to replace an earlier choice.
   Return the original AHPS bytes without a wrapper if it wins or ties. Store
   and decode this selected output too; it is selection, not a fourth encode.

If the declared table is unavailable, run/verify AHPS, explicitly mark both
aligned candidates unavailable, and return the original archive. Candidate
errors or timeouts remain failures, not disguised successful fallbacks.

Outside fragments are transformed afresh from ORIGINAL file bytes. Never slice
an existing residual: SPLIT and EXE_SPLIT move bytes. Preserve copied programs,
but add fragment.start - original_segment.start to every EXE32/EXE_SPLIT base,
modulo 2^32. Uncut segments retain identical parameters. Other predictors and
scanner history restart at a new fragment boundary; this changes residuals and
is part of the aligned control. Do not claim outside residuals equal baseline.
The byte and word controls must have identical outside descriptors and lengths.

All three archives use one solid XZ stream. Count every header, descriptor,
checksum and XZ byte. The aligned-byte control shares the new research magic
with aligned-word, isolating the table substitution within matched framing.
Its difference from original AHPS includes boundaries, predictor resets and
framing, not word arithmetic. A selected aligned-byte win is not a word win.

## Research codec contract

Implement `research/relational_hybrid.py` independently. Research magic is
`b'AHRH\x01'`; production v5 is never written with the new transform. The container
uses the existing 25-byte header and length/program table layout:
`[magic:5][segments:u32][table_bytes:u32][total_bytes:u64][CRC32:u32]`, followed
by `[length:u32][steps:u8]` and up to three `[opcode:u8][params:4]` per segment,
then all segment residuals in order. CRC32 covers the restored complete file.
There are at most 65,536 segments, at most 20*65,536 table bytes, and exact
contiguous coverage. Empty input has zero segments and zero table bytes.

All existing v5 opcodes retain their semantics. New opcode 10 subtracts the
previous record's corresponding uint32 modulo 2^32, leaves the first 12-byte
record intact and keeps interleaved bytes. Parameters are exactly `0c000000`.
It is legal only in the canonical two-step program
`[(10,0c000000),(7,0c000000)]`, on at most one segment with positive length
divisible by 12 and no more than 8 MiB. SPLIT remains the existing 12-byte lane
permutation. This avoids ambiguity about word width, warmup or shuffle order.

The buffered research decoder accepts only AHPS v5 and AHRH1. Require exactly
one CRC64 XZ stream, dictionary memory no more than 128 MiB, compressed input
no more than 160 MiB, and restored output within both the caller's bound and
128 MiB. Validate the decoded header and declared counts/lengths before a large
expansion or output allocation; bound actual expansion too. Reject truncated,
trailing/concatenated streams, invalid magic, unknown opcodes, malformed word
programs, excessive counts/lengths, incorrect coverage and restored CRC errors
before returning bytes. Validate all descriptors before invoking native code.
Native execution requires actual ABI 206; Python is an explicit test option.
Decode uses only recorded transforms, with no PE parsing or predictor search.

Expose helpers for the baseline archive/serialized plan, aligned plans, research
plan encoding, strict inspection/decoding, stable archive selection, and a
convenience encoder that runs the three candidates once. Selection metadata
must name the winning representation and complete candidate sizes. This is a
bounded research API; no streaming or production-performance claim is made.

## Validation and execution

Test independent carry/borrow/wrap vectors and wire fixtures, native/Python
parity, full-file round trips, exact fallback/ties, one baseline call, fragment
coverage, nonzero/wrapping executable origins, fresh-transform resets, illegal
programs, CRC rejection and actual bounded expansion. Run relevant existing
regressions. Tests, preparation, controllers, workers and audit hashing set and
verify Windows BELOW_NORMAL (`0x4000`) before heavy work. Do not overlap tests
or other benchmarks with this comparison.

One worker per complete file, serial under the shared benchmark lock. Reuse the
verified Windows Job runner: **300 seconds per file, 900 seconds per batch,
2 GiB aggregate committed memory**, no breakaway and full tree cleanup. Preserve
every started, failed, timed-out, unavailable and unstarted slot. No retries,
retuning or silent limit extensions. Use a new immutable experiment directory
and report; freeze native/source/test/protocol/runtime identities and complete
inputs. All five available tables yield **15 measured archive encodes and 20
verified output round trips**, including the five selected copies.

Record actual serialized baseline and candidate plans, source/archives/restored
hashes and sizes, priorities, phase times and Job outcomes. Time baseline encode,
plan recovery, both candidate builds/encodes, and selection separately. The
hybrid's encode cost includes all those components; never present the trivial
selection time as end-to-end encoding time. Exclude metadata-only serialization,
benchmark verification and artifact I/O from that component sum and label it
explicitly; it is not a timed invocation of the convenience encoder. Decode the
reference with the unchanged production decoder and candidates/selected output
with the research decoder, recording the actual API. Single-pass times
while the maintainer uses the PC are diagnostic; shared Job peaks are not per-method
memory comparisons. No extra backend matrix or throughput reruns.

Report original, aligned-byte, aligned-word and selected sizes per complete
file. Separate word-versus-byte effects from selected-versus-original effects.
Verify selected size never exceeds original and that a fallback is byte-for-byte
identical. Compute a five-file aggregate only if every required comparison
finishes; retain all missing results otherwise. Do not extrapolate earlier table
percentages, count a boundary-only win as word arithmetic, or call a selected
non-regression a novel compression gain. Preserve negative and mixed outcomes.
No production adoption, push, model search expansion or follow-up tuning is
authorized automatically by these results.
