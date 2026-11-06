# AHPS Relational (AHRL): complete development-folder test

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026. The maintainer requested testing against the exact local directory
`<repo>/corpus_development`. The measurement host was in interactive use during this study. Workers ran
serially at below-normal priority, and timings were treated as diagnostic.
No web research, downloads, sealed inputs or alternative directories are needed.

## Scope fixed before results

Test every byte of the five files, in filename order: `7zip-cli.exe`,
`chrome.exe`, `git-cli.exe`, `node.exe`, `vlc.exe`. Verify each against its
development-manifest size/SHA-256 and snapshot it before timing. Reject changes
or unexpected entries rather than silently selecting a subset. Node is about
91 MB and must not be replaced by a slice. Preserve every outcome and timeout.

The existing AHRL codec requires records no larger than an 8 MiB region. Add a
small independent file wrapper around the unchanged `research/relational.py`.
Use width=4, fields=3 throughout every executable, with chunks of 8,388,600
bytes (the largest whole-record multiple below 8 MiB). This is deliberately a
**blind fixed-layout whole-file test**, not automatic executable understanding.
Executables are not uniformly three-field records. Report this limit prominently.
Do not tune widths, alignment, models or chunk sizes after observing results.

For each complete file compare, in this order:

1. Bare solid XZ, preset 6, CRC64.
2. AHRL-file RAW: one AHRL `raw` archive per fixed chunk, same file wrapper.
3. AHRL-file AUTO: one AHRL `auto` archive per fixed chunk, same file wrapper.
4. Frozen production AHPS, preset 6, block 4096, native ABI 206, `FAST_CHUNKS`.

The matched RAW control isolates benefits of representation search. Solid XZ
and AHPS include the practical effects of chunk resets, metadata and framing.
Do not describe gains over matched RAW as gains over AHPS. Count complete output
bytes and verify complete restored length and SHA-256 for every method.

## File-wrapper contract

Implement `research/relational_file.py`, with `encode(data, method='auto',
width=4, fields=3, chunk_bytes=8388600, preset=6) -> bytes`,
`decode(archive, max_output_bytes=128*1024**2) -> bytes`, and validated metadata
inspection. This is a buffered, bounded research API, not streaming production.
Cap sources at 128 MiB and the number of frames at 4096. Permit smaller valid
chunk sizes for unit tests; the benchmark must explicitly use the fixed size.

File magic is `b'AHRF\x00\x00\x00\x01'`. Header `<8sBBBBIQII` stores magic, width,
fields, two zero reserved bytes, chunk_bytes, total original bytes, frame count,
and CRC32 of all original bytes. Then each frame has `<II` (original bytes,
stored AHRL archive bytes), followed by that archive. There is no outer XZ.
Each nonfinal frame has exactly chunk_bytes original bytes; the last has the
remaining length. Pad only a final partial record with at most record_bytes-1
zero bytes before passing it to AHRL. Decode checks the exact padding and trims
to the stored original length. A zero-length file has zero frames.

Validate dimensions and exact chunk/count/total relationships before allocation.
Every frame must match the declared width/fields, decoded padded length and
canonical original length. Cap a stored frame to 9 MiB and total archive input
to 160 MiB. Reject bad magic/reserved bytes, length overflow, excessive output,
missing/extra frames, nonzero padding, corrupt inner archives, mismatched layout,
CRC failure and trailing bytes. The wrapper may use the existing codec's
validated internal container/restoration helpers to avoid decoding a frame twice;
do not change those helpers or bypass their limits/validation. Decode performs
no predictor search. Both RAW and AUTO include all identical wrapper costs.

## Execution and evidence

Use a separate immutable artifact directory and report, preserving earlier
cohorts. Pin source snapshots, this protocol, tests, baseline/runtime binaries,
source snapshots and original-file identities. Validate parent hashes before
and after copying. Require actual worker priority 0x4000 (BELOW_NORMAL), recorded
and verified before encoding; no silent normal-priority fallback.

One worker per file, four methods serially, under the shared benchmark lock.
Reuse the corrected Windows Job runner: **300 seconds per file, 900 seconds
total, 2 GiB aggregate committed memory**, no breakaway, full tree cleanup.
Do not run competing benchmarks or tests. One observation per method/file;
record API encode/decode seconds and Job-wide memory as diagnostics, not stable
speed or per-method memory comparisons. User activity is an explicit confounder.

Record exact model/frame selections, archive/restored hashes and byte counts,
errors, unstarted slots, deadlines and resource outcomes. AUTO must equal the
byte-size-or-better matched RAW bound, but may exceed solid XZ/AHPS. No automatic
production adoption or tuning follows. Summarize per-file differences against
AHPS and matched RAW separately; totals describe only this specified five-file
set. This user-requested corpus screen is not a reversal of the earlier failed
cross-field continuation criterion and is not final holdout evaluation.
