# Research codecs and experiments

This directory contains experimental codecs and reproducibility harnesses.
Their archive formats, size limits, native dependencies, and recorded comparisons
are described below. The [documentation index](../docs/index.md) links the
protocols, outcomes, and historical audits.

Production CSV/v6 and safetensors/v7 support is implemented in the root AHPS
modules and exposed by `ahps.py --help`. Single-file benchmarks support
`--ahps-only`, and terminal progress is controlled with `--progress` or
`--no-progress`. See the [current usage documentation](../README.md) and
[format contract](../docs/format.md).

Research harnesses often depend on a local development corpus, pinned tool
builds, and existing experiment records. These inputs, binary archives, and raw
logs may be absent from a public checkout. Recorded commands describe their
original cohorts. Input identities, settings, and limits must be established
for any new comparison so its results can be interpreted independently.

## AHPS Relational (AHRL)

AHPS Relational is the working name of this research prototype. `AHRL` is its
record-container signature; `AHRF` identifies the optional whole-file wrapper,
and `AHRH1` is the separate selective solid-stream hybrid format.
`AHRC1` identifies the newer independent LZ77/arithmetic backend; unlike the
earlier formats, it does not use LZMA2 or an AHPS archive fallback.

This local research codec encodes known layouts of equal-width integer fields.
It tests whether causal relationships between fields add compression value.
The relational experiment uses its own codec and archive format. The design and
comparison rules are in [the experiment contract](../docs/relational-compression.md).

```python
import struct
from research.relational import encode, decode, inspect_archive

records = b"".join(struct.pack("<II", x, 3 * x + 7) for x in range(1000))
archive = encode(records, width=4, fields=2, method="auto")
assert decode(archive) == records
print(inspect_archive(archive))
```

Widths are 1, 2, 4 or 8 bytes; there may be 1..16 fields per record. Input must
contain complete records and fit the 8 MiB prototype cap. All original bits
are preserved, including when the words contain floating-point encodings.
There is no numerical rounding and no automatic layout detection.

Methods `raw`, `shuffle`, `delta`, `independent`, `cross`, and `adaptive` encode
one representation. `independent` selects per-field models with no dependency
on other field values; it is the control for cross-field prediction. `auto`
performs all six complete encodes and returns the smallest archive, counting
headers, models and XZ framing. This search has an explicit time cost. Decode
performs only the recorded transforms, without a model search.

The archive is an independent `AHRL` container inside one CRC64 XZ stream.
Use the research decoder for these archives. These functions buffer bounded
regions; they do not promise streaming or production performance.

Run correctness checks from the repository root:

```text
python -B -m unittest tests.test_relational -v
```

The first comparison uses a pinned Blender development table, two planted
controls and independent random fields. Its one-time sequence is:

```text
python -B research/compare_relational.py prepare
python -B research/compare_relational.py run
python -B research/compare_relational.py summarize
```

Preparation freezes sources, input bytes, settings and runtime. Commands refuse
to overwrite existing experiment records. The Windows Job runner bounds one
input worker to 120 seconds and 2 GiB committed memory, under a 600-second batch
deadline and the shared benchmark lock. All nine methods and every failed or
unstarted result remain visible. A new experiment needs its own declared inputs,
output locations and frozen protocol; the first results must not be overwritten.

The original table exceeded its 2 MiB eligibility cap. A separately declared
[table-only follow-up](../docs/relational-table-followup.md) uses 3 MiB, the same
codec and limits, and distinct artifacts. Its command sequence appends
`--profile table-only` to each of the three commands above. Both cohorts have
already been recorded; see the [results](../reports/relational-compression.md).

## Whole-file testing

`research.relational_file` wraps the unchanged record codec in bounded chunks,
preserves incomplete final records, and supports complete files up to 128 MiB.
Its `encode(data, method='auto')` and `decode(archive)` byte APIs use independent
file framing and validate every inner archive plus the restored whole-file CRC.
`inspect_archive` returns validated chunk/model metadata. These APIs buffer the
file; they are not streaming interfaces.

The default layout is three little-endian uint32 fields and 8,388,600-byte
chunks. Applied to an arbitrary executable, this is a blind-layout test: the
prototype does not discover executable structure. The [development-folder
protocol](../docs/relational-corpus-experiment.md) compares complete-file AUTO,
identically chunked RAW, solid XZ and production AHPS, including every framing
byte. It keeps workers serial and at below-normal priority during interactive use of
the measurement host; its single-pass times are diagnostic.

The [corpus results](../reports/relational-corpus.md) retain all 20 planned cells,
including Node's timeout. The one-time harness sequence is
`python -B research/compare_relational_corpus.py prepare`, then `run`, then
`summarize`. Existing evidence is immutable; these commands refuse to overwrite
or resume the recorded run. Its matched RAW control is separate from the solid
XZ and AHPS comparisons.

## Same-boundary table comparison

The [table protocol](../docs/same-boundary-table-experiment.md) compares complete
exception directories from the five development-folder files and the reused
Blender reference. Fixed AHPS RAW, SPLIT12, D1/SPLIT12 and D2/SPLIT12 use ordinary
v5 archives. AHRL word delta, automatic AHPS and bare XZ complete the seven-method
comparison; separate residual-only probes isolate byte versus word arithmetic.
No codec or planner is changed.

The [results](../reports/same-boundary-tables.md) contain 42 passing archive round
trips and 30 passing residual checks. Word delta wins on four of five development
tables and Blender, while Chrome favors AHPS. Automatic AHPS already produces
the same archive as the best fixed control on all six tables. These are table
results, not whole-file gains.

The recorded one-time sequence is
`python -B research/compare_same_boundary_tables.py prepare`, then `run`, then
`summarize`. The serial, below-normal run has 120 seconds per table, 600 seconds
per batch and a 2 GiB Job memory limit. Commands refuse to overwrite the saved
experiment; subsequent studies need a separately declared protocol and location.

## Selective whole-file hybrid

`research.relational_hybrid` keeps the unchanged AHPS archive as an exact
fallback and tests two aligned plans around one known exception table. The
controls share all outside fragments: one uses existing byte delta, the other
uses uint32 word delta; both finish the table with SPLIT(12). Each candidate is
one solid XZ stream, and selection counts every archive byte. Ties keep AHPS.

```python
from research.relational_hybrid import encode, decode

# data is the complete source; offset/length identify its validated table.
archive, selection = encode(data, table_start=offset, table_length=length)
assert decode(archive) == data
print(selection["selected_name"], selection["candidate_bytes"])
```

The API requires explicit table boundaries; the comparison harness obtains them
from the validated PE exception directory. It does not scan for profitable
regions. Omitting both table arguments returns the original AHPS archive.
Invalid bounds or candidate failures raise errors instead of pretending a
successful search. The source cap is 128 MiB and the table cap is 8 MiB.

Both aligned controls use the separate `AHRH1` research format. Use the hybrid
decoder for selected output: it accepts AHRH1 and ordinary AHPS v5 fallbacks.
The production AHPS decoder does not accept AHRH1. Existing opcodes and the
native ABI remain unchanged; the research word transform runs in Python.

The [frozen whole-file protocol](../docs/selective-word-hybrid-experiment.md)
defines the controls, boundary resets, format, decoder limits and execution
rules. The one-time harness is `research/compare_relational_hybrid.py`, with
`prepare`, `run` and `summarize` commands. It runs the exact five development
files serially at below-normal priority, under a 300-second per-file limit,
900-second batch limit and 2 GiB Job committed-memory cap. The selected archive
requires all three encodes to choose it; selection time alone is not its
encoding cost. Saved experiments cannot be overwritten or resumed.

The [complete-file results](../reports/relational-hybrid.md) contain 15 encodes
and 20 verified outputs. All five selected archives are smaller than frozen
AHPS, totaling 22,828 bytes saved (0.090%). Four choose word delta and Chrome
chooses byte delta. Word delta adds 3,836 bytes of saving beyond choosing the
smaller of original AHPS and aligned byte delta. These are modest development
results with extra encode work, not production adoption.

## Independent AHRC backend

The separate `codex/independent-compression-backend` branch adds
`research.independent_backend` and a locally implemented C LZ77/arithmetic
coder. It imports no old Python planner or compression library. It reuses
only the frozen native AHPS reversible lifts, plus its own native word delta.

```python
from research.independent_backend import encode, decode

archive, selection = encode(data)  # Tries original bytes and whole-file EXE_SPLIT.
assert decode(archive) == data
print(selection["selected_representation"], selection["candidate_bytes"])
```

For a validated complete exception table, pass `table_start=offset` and
`table_length=length` to include both byte- and word-delta table candidates.
The API does no PE parsing or automatic layout discovery. `encode_variant`
also exposes `raw`, `exe`, `table_byte` and `table_word` individually.
Every candidate uses AHRC framing and its own coded-or-stored payload. The
stored fallback caps the result at source size + 52 bytes; it does not promise
to match AHPS. Selected output requires the independent decoder.

Sources are capped at 128 MiB and tables at 8 MiB. The current build requires
Windows and the frozen AVX2 transform DLL for transformed representations.
`research/build_ahrc_backend.py` builds the separate backend DLL and records its
compiler, source/output hashes and resolved Windows runtime dependencies.
Rebuilding changes build evidence; preserve frozen experiment artifacts.

The [design and wire contract](../docs/independent-backend-experiment.md) and
[first results](../reports/independent-backend.md) document 135 passing targeted
tests, 25 encode operations and 30 verified output round trips. All five
selected independent archives are larger than AHPS, by 24.32% overall. The
backend is functional and independent, with no production adoption or novelty
claim. The earlier LZMA2-backed hybrid's size gain is a separate result.

The one-time comparison harness is `research/compare_independent_backend.py`
with `prepare`, `run` and `summarize`. It checks the exact five complete
development files, runs serially below normal priority with 300 seconds per
file, 900 seconds per batch and a 2 GiB Job cap, and refuses to overwrite or
resume saved measurements. Its AHPS baseline phase uses LZMA2; all independent
candidate phases are guarded against old-codec/planner calls.

## Generic model-weight comparisons

`research.weight_transforms` implements exact bit-plane and FP8 field layouts
for bounded byte buffers. `research.weight_compression` wraps those transforms
with available generic codecs in the separate AHWT research format. Optional
codec dependencies and native executables are needed only for the methods that
use them. These helper inputs are capped at 32 MiB.

The [generic comparison](../reports/weight-chunks.md) records three local 15 MiB
model slices and the settings used by `research.compare_weight_chunks`.
It preceded the [integrated tensor feature](../reports/safetensors-aware.md),
which uses safetensors metadata and the production AHPS v7 decoder. AHWT archives
use the research helper's decoder. The model and extracted slices may be absent
from a public checkout.
