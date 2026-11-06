# Relational compression research: first bounded prototype

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

Authorized locally on November 3, 2026, on `codex/relational-compression`, based
on `main` at `0dac8d2`. The production AHPS codec, DLL, format and defaults stay
at that baseline. This document defines the first experiment before results.

## Question and constraints

Can exact prediction between fields of repeated records improve complete
compressed size beyond RAW, byte shuffle, per-field integer delta, and current
AHPS on the same bytes? A planted relation validates the mechanism; a real
development region is required for any useful-data claim. This is an isolated
research codec, not an AHPS format extension or a production replacement.

The existing [format contract](format.md) and [development/holdout separation](../reports/evaluation-protocol.md)
remain in force. No online research, downloads, sealed files or reserves are
needed. Previous experiments established that small-window rankings can reverse
on whole streams, so report every complete archive, including metadata.

## Prototype contract

Implement `research/relational.py` using only the standard library. The API is
`encode(data, width, fields, method, preset=6) -> bytes` and
`decode(archive, max_output_bytes=8*1024**2) -> bytes`. Support methods `raw`,
`shuffle`, `delta`, `independent`, `cross`, `adaptive`, and `auto`. `auto` chooses
the shortest complete archive among those six methods, with that order breaking ties.
Expose `inspect_archive(archive) -> dict` for validated layout/model metadata.
The research format is independent of AHPS and must reject AHPS archives.

Records contain 1..16 little-endian unsigned fields, all width 1, 2, 4 or 8
bytes. Require an exact record multiple, at most 8 MiB of source data. Preserve
all bit patterns; there is no float arithmetic. Prediction is modular integer
arithmetic: residual = (value - prediction) modulo 2^(8*width). Restore with
addition. All dependencies are earlier fields of the same record or the same
field of the previous record, initialized to zero. No coefficient inversion is
required during reconstruction. Decode must perform no model search.

XZ with CRC64 and preset 6 wraps a container. Header `struct <8sBBBBQI` contains
magic `b'AHRL\x00\x00\x00\x01'`, width, fields, storage mode, zero reserved byte,
source byte count, and CRC32 of original bytes. Storage modes are 0 interleaved
RAW, 1 byte-shuffled unmodeled values, and 2 modeled then byte-shuffled residuals.
Shuffle concatenates each byte-position lane of the entire record in order.
Mode 2 stores exactly one `<BBQQ` descriptor per field: kind, source index,
coefficient a, intercept b. Kinds: 0 raw (src/a/b zero), 1 previous same field
(src/a/b zero), 2 same-record affine `a*x[src]+b` (src strictly earlier, a/b fit
the word width). No padding, concatenated XZ streams or trailing bytes allowed.

`delta` uses previous-field history for every field. `cross` uses previous
history for field zero and current preceding field (a=1,b=0) for the rest.
`adaptive` selects a model independently for each field with a bounded screen:
fit the first at most 1024 records; score the following at most 1024 records,
or reuse the fit region when no following records exist (explicitly a training
heuristic, not independent evaluation). Candidates are raw, previous history,
and affine models from every earlier field. Coefficients are 0,1,-1,2,-2,3,4,8,16
modulo word width, plus at most 16 slopes derived from consecutive fit-record
pairs with odd source differences using exact modular inversion. Fit each
intercept as the modal modular difference, breaking ties by smallest value.
Rank by summed zero-order byte-lane residual costs, then stable candidate order.
`independent` is an ablation using exactly the same adaptive selector but limiting
affine candidates to a=0 (constants), alongside raw and previous history. It
preserves constant-candidate availability per field and separates model-choice
benefits from relationships between fields. It introduces no new wire kind.
This heuristic is not a whole-stream compression prediction. Complete archives
of all methods are measured; `auto` cannot exceed framed RAW size but has search
cost and framing overhead relative to bare XZ.

Decoder: validate dimensions, modes, descriptors, causal references and lengths
before reconstructing; limit XZ memory to 128 MiB and actual expanded container
bytes to allowed output plus maximum header/descriptors plus one overrun byte.
Reject truncation, checksum failures, trailing/concatenated streams, invalid
models and excessive declarations. Bounded buffering is sufficient for this
prototype; do not claim a streaming or production-memory improvement.

## Fixed first comparison

Use four inputs in this order:

1. Complete x64 exception-directory table from the pinned Blender development
   PE, interpreted as three uint32 fields (begin RVA, end RVA, unwind RVA).
   Verify full parent SHA-256 against the development manifest before extraction.
   Validate PE32+ x64 identity, directory/section bounds and record length. If
   absent, malformed or above 2 MiB, record an unavailable outcome; do not choose
   another region after results.
2. Planted exact relation: 16,384 three-uint32 records; x from a local
   `random.Random(20261103)`, y=3*x+7, z=-5*y+11, modulo 2^32.
3. Planted noisy relation: same generator seed and relation, except every 32nd
   record (1-based) adds a uniformly drawn 8-bit modular error to y, computes z
   from that observed y, then adds an independently drawn 8-bit error to z.
4. Independent fields: three random uint32 values per record, same record
   count and independent local `random.Random(20261104)`.

Each input is a complete region/control, not a whole executable. Encode the
six fixed research methods and `auto`, bare XZ preset 6 CRC64, and baseline
AHPS with explicit preset 6, block size 4096, native ABI 206 and `FAST_CHUNKS`.
All use the same runtime and backend. Verify every restoration by length and
SHA-256. Record source/code/runtime identities, archive hashes and bytes, model
metadata, actual API encode/decode times, and every failure. Single-pass timings
are diagnostic only. Archive bytes include all model and framing costs.

Run one worker at a time under the shared benchmark lock. Reuse the corrected
Windows Job runner, with a 600-second batch deadline, 120 seconds per input,
and 2 GiB aggregate committed memory. Retain all attempt records on timeout;
do not silently repeat. No full-file encode or broad parameter sweep is needed.

Continuation criterion, fixed before measurement: the real Blender table's
best cross-field method (`cross`/`adaptive`) must be at least 1% smaller than
the best of bare XZ, AHPS, research raw/shuffle/delta/independent; the winning
cross-field method must actually use a nonzero-a same-record model, and all
round trips must pass.
This warrants another independent development input, not production adoption
or a whole-file claim. Synthetic results alone cannot pass this criterion.
Report predictor gains separately from integer-delta/shuffle gains. A miss
is retained as evidence and does not justify retuning this first cohort.
