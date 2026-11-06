# Independent AHRC backend experiment

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026. The maintainer requested an actual backend without LZMA2, then
requested a different branch. Work is on `codex/independent-compression-backend`
from committed research checkpoint `3788ed9`. The earlier research branch and
production AHPS remain unchanged. This design replaces the earlier fixed-XZ
research constraint for this new experiment only. No online research is used.

## Scope and independence

Implement locally authored LZ77 matching, probability models and binary
arithmetic coding in `research/ahrc_backend.c`, built as a separate DLL. The
Python API in `research/independent_backend.py` must not import or invoke AHPS
planning, the earlier codecs, LZMA/LZMA2, XZ, Deflate, Zstd or another compressor.
It may call the frozen AHPS native library's reversible EXE_SPLIT, DELTA1 and
SPLIT lifts directly, with ABI/CPU checks, and use `binascii.crc32` for checksums.
Those functions are transforms, not compression backends. New word arithmetic
also runs in the new C DLL. Neither DLL may call an external compression codec.

AHPS is a separately measured baseline only. Never select or embed an AHPS/XZ
archive as the candidate fallback: use the new format's stored-payload mode.
Test encoder/decoder operation in a fresh process that denies imports of the
old Python planner and compression libraries, and guard compression entry points
during candidate benchmark phases. Inspect and pin native build dependencies.
Reusing established LZ77/arithmetic-coding ideas is not a novelty claim.

## Fixed backend version 1

Cap source/output at 128 MiB. Match against one continuous 8 MiB sliding history,
with 2^19 hash heads, a window-sized position ring, and at most 32 chain probes.
Hash four little-endian bytes by unsigned multiplication by 2654435761 followed
by a 13-bit right shift. Use UINT32_MAX as the absent-position sentinel. Check
history distance and strictly descending positions before following chain links.
Use greedy parsing, minimum match 4, maximum match 65,535. Try the last distance
first and retain it on equal match lengths; initialize it to 1. Insert every
consumed position deterministically, including positions skipped by a match.
Overlapping decoder matches must copy forward one byte at a time.

Tokens have a literal/match bit, context `(output_position & 3)*2 + previous_match`,
with previous_match initially zero. Literals use eight MSB-first bits in a binary
tree conditioned on the previous reconstructed byte (zero at file start).
There are 256 contexts, each with tree nodes indexed from 1 through 255. For a
match (flag 1), code a repeat-distance bit with context `output_position & 3`;
a repeat (bit 1) uses the last distance, otherwise code the positive distance
and update it. Then code length as the positive integer `length-3`. A literal
has flag 0. The previous-match context changes only after the complete token.

Positive integer coding uses `k=floor(log2(value))`: k one bits then one zero
through prefix models indexed 0..k, followed by k lower bits MSB-first through
suffix models indexed by (k, bit position), visiting bit positions k-1 down to
zero. Use separate prefix[24] and
suffix[24][24] arrays for lengths and distances. Distances are 1..8,388,608;
length values are 1..65,532. All model probabilities represent zero probability,
start at 2048 out of 4096, and update after each bit: zero adds `(4096-p)>>5`,
one subtracts `p>>5`. Model state persists across the complete representation.

Use unsigned 32-bit inclusive low/high binary arithmetic coding with uint64
intermediates. Split at `low + (((high-low+1)*p)>>12) - 1`. Renormalize using the
standard lower-half, upper-half and middle-half cases, carrying pending opposite
bits for the middle-half case. Finish by incrementing the pending count and
emitting the bit selected by low relative to the quarter boundary, followed by
pending opposite bits and 30 zero bits. This yields the decoder's initial 32
bits plus one consumed bit per renormalization. Payload is a little-endian u64
valid-bit count followed by MSB-first packed bits, with zero unused low bits.
The decoder requires at least 32 valid bits, exact byte length, zero last 30
bits/padding, and exact bit consumption after the declared output is complete.
All bit reads/writes, output lengths and distances are bounded; reject invalid
or truncated streams. Tests must independently verify finalization and wire
interpretation before measurements are frozen.

Export backend ABI version 1, bounded encode/decode functions with explicit
capacities/status codes, and a word-delta helper. Compile separately using
GCC `-O3 -std=c11 -Wall -Wextra -Werror -shared -static-libgcc
-Wl,--no-insert-timestamp`, without new CPU-specific instructions. Record actual
compiler/version/command/source/DLL/dependency identities in a build manifest.
Build and synthetic development checks run below normal priority.

## Candidate representations and format

No old planner or LZMA-derived plan is used. Test these fixed representations
in order, all using the same continuous backend:

1. `raw`: unchanged complete source bytes.
2. `exe`: one whole-file EXE_SPLIT with base zero.
3. `table_byte`: EXE_SPLIT on prefix and suffix, with bases zero and table_end;
   the entire declared table uses DELTA1(12,0), then SPLIT(12).
4. `table_word`: identical boundaries and outside transforms, with uint32 delta
   across corresponding fields of adjacent 12-byte records, then SPLIT(12).

Word delta is modulo 2^32, with the first record preserved. Empty outside
regions are omitted. Derive table ranges only through the existing validated
x64 PE exception-directory parser; maximum table length 8 MiB, positive and
divisible by 12. No slices, padding, alternate regions, predictor or layout
search. The public candidate API accepts explicit bounds, not a PE parser or
an AHPS archive. Without bounds, only raw/exe are eligible. Byte and word controls
isolate the table substitution at identical boundaries; exe versus table modes
also changes boundaries and scanner history.

New header is `<8sBBBBQQQQII>` (52 bytes): magic `b'AHRC\0\0\0\1'`, backend mode
(0 stored, 1 coded), representation (0 raw, 1 exe, 2 table_byte, 3 table_word),
two zero reserved bytes, original length, payload length, table offset, table
length, original CRC32, and transformed-residual CRC32. Non-table representations
must have zero table fields. Table modes require valid complete in-file records.
Following payload length must exactly equal the remaining archive bytes.

Stored mode copies the transformed residual and has payload length equal to
original length. For each representation, use coded mode only when its entire
payload is strictly smaller than the residual. A bounded encoder capacity
exhaustion means stored mode; allocation/invalid-input failures remain errors.
This caps every variant at original length + 52 bytes. Select the smallest
complete candidate in raw/exe/table_byte/table_word order, preserving earlier
ties. The output always uses AHRC; it never falls back to AHPS or another codec.

Decode validates magic/version/reserved bytes, mode/representation, source cap,
caller output cap, payload lengths, table dimensions, actual backend expansion,
match bounds and both CRCs before returning bytes. It performs no model search
or source-format parsing. Reject trailing/concatenated archives and unknown
versions. Empty source uses the canonical raw stored representation. Do not
allocate output until the uncompressed size and descriptor bounds are checked.

## Frozen complete-file comparison

Use exactly `corpus_development/7zip-cli.exe`, `chrome.exe`, `git-cli.exe`,
`node.exe`, `vlc.exe`, in that order, with every original byte. Verify complete
parent and snapshot sizes/SHA-256 against the development manifest before and
after copying/extraction/running. No Blender, holdouts, reserves, downloads or
additional files. These are reused development inputs, not independent finals.

For each file, first encode/decode unchanged production AHPS at preset 6,
block 4096, explicit FAST_CHUNKS, default features and native ABI 206. All five
must reproduce the frozen baseline archives in `relational-hybrid-20261103.json`,
including Node. Then encode/decode the four independent variants and the selected
copy. All available tables give **25 candidate/baseline encode operations and
30 output round trips**; selected copies are not a fifth independent encode.
Report representation trials that choose stored mode explicitly. If a table is
unavailable, retain both unavailable slots and select among raw/exe. Errors and
timeouts must never become disguised successful fallbacks.

Freeze new code/DLL/build/compiler/protocol/tests, old native transforms and
baseline code, runtime, manifest and historical baseline evidence before the
first measurement. Use a new immutable artifact directory/report. Preserve
all original, transformed/residual identities where recorded, archive/output
hashes, exact sizes, selected representations/backend modes, API component
times, native identities, priority readbacks and resource outcomes. Report
backend-only raw versus transformed representations separately from AHPS.

Controllers, tests, workers and audit hashing set and verify Windows BELOW_NORMAL
priority (`0x4000`) before heavy work. Keep one file worker at a time under the
shared benchmark lock, with **300 seconds per file, 900 seconds per batch,
2 GiB aggregate Job committed memory**, no breakaway and full owned-tree cleanup.
No competing tests/benchmarks, automatic retries, post-result tuning or silent
limit extensions. Keep unavailable, failed and unstarted slots in the report.
Compute complete five-file totals only if every required comparison finishes.

The candidate encode cost is the sum of its eligible representation trial APIs
plus selection, excluding baseline AHPS, benchmark verification, metadata-only
serialization and artifact I/O. It is a component sum, not a separately timed
convenience API call. All timing is diagnostic while the maintainer uses the PC;
shared Job peaks are not per-codec memory comparisons. Losses against AHPS are
valid outcomes. A working independent backend does not imply it beats AHPS,
LZMA2 or other compressors, and no production adoption follows automatically.
