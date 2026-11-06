# Selective word-delta hybrid: complete-file results

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. The research hybrid produced smaller complete archives than
the frozen AHPS baseline on **all five development files**. It saved **22,828
bytes overall, or 0.090037%** of the baseline archives. This is a small verified
whole-file improvement on this reused development set. Most of the saving is
already available from the aligned existing byte-delta control; word delta adds
a smaller incremental benefit.

The implementation, [frozen protocol](../docs/selective-word-hybrid-experiment.md),
complete measurement record (local record `relational-hybrid-20261103.json`), and
[independent audit](relational-hybrid-audit-20261103.md) remain on
`codex/relational-compression`. Production AHPS Python/C/DLL, defaults, native
ABI 206 and existing AHRL codecs are unchanged.

## Whole-file archive sizes

All values count every header, descriptor, checksum and XZ byte. Each archive
uses one solid preset-6 CRC64 XZ stream. The aligned controls share the same
research framing and outside programs; their table programs differ only in
byte versus uint32 delta before the same SPLIT(12). Selection considers the
original AHPS archive and both controls, with ties preserving the earlier
choice. Lower is better.

| Complete file | Frozen AHPS | Aligned byte delta | Aligned word delta | Selected output | Selected saving | Selected method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 7zip-cli.exe | 181,776 | 181,616 | 181,492 | 181,492 | 0.156236% | Word |
| chrome.exe | 1,628,332 | 1,621,304 | 1,621,476 | 1,621,304 | 0.431607% | Byte |
| git-cli.exe | 1,473,840 | 1,462,352 | 1,460,872 | 1,460,872 | 0.879878% | Word |
| node.exe | 21,768,312 | 21,769,004 | 21,766,840 | 21,766,840 | 0.006762% | Word |
| vlc.exe | 301,752 | 301,436 | 300,676 | 300,676 | 0.356584% | Word |
| Sum of these five files | 25,354,012 | 25,335,712 | 25,331,356 | 25,331,184 | 0.090037% | Four word, one byte |

The complete sources total **101,626,280 bytes**. Their individual sizes are
575,488; 4,522,648; 4,055,416; 91,426,304; and 1,046,424 bytes, respectively.
Each source and snapshot matched the development manifest before and after the
run. No file was replaced by a slice. The complete table lengths were 30,036;
89,580; 85,260; 830,904; and 44,748 bytes. These tables identify the candidate
regions; every measured archive restores the entire parent executable.

All four previously completed AHPS baselines reproduced their saved archive
lengths and SHA-256 hashes exactly. Node's AHPS baseline is a new completed
measurement: the earlier whole-file screen never reached that method. The
earlier timeout and other experiments remain unchanged.

## What word delta contributes

The aligned-byte comparison includes new boundaries, predictor/scanner resets
and research framing. Its improvement over original AHPS cannot all be credited
to word arithmetic. Chrome chooses byte delta, and Node's aligned-byte archive
is 692 bytes larger than original AHPS.

| File | Word saving vs aligned byte, bytes | Selected saving vs original, bytes | Word's extra saving beyond the smaller of original/byte, bytes |
| --- | ---: | ---: | ---: |
| 7zip-cli.exe | 124 | 284 | 124 |
| chrome.exe | -172 | 7,028 | 0 |
| git-cli.exe | 1,480 | 12,968 | 1,480 |
| node.exe | 2,164 | 1,472 | 1,472 |
| vlc.exe | 760 | 1,076 | 760 |
| Sum | 4,356 | 22,828 | 3,836 |

The last column is a derived comparison using the same measured archives,
with no extra encodes or new candidate search. An existing-transform selector
choosing the smaller of original AHPS and aligned byte would total **25,335,020
bytes**, saving **18,992 bytes** already. Allowing word delta reduces that by
another **3,836 bytes**, or **0.015130%** of the original AHPS total. Thus the
record arithmetic has measurable incremental value here, while the larger
opportunity in these results lies in the aligned existing-transform control.

The whole-file savings are much smaller than the earlier isolated-table
percentages. Word delta remains optional: forcing it would lose 172 bytes on
Chrome relative to the aligned byte control. The original archive remains an
exact fallback for any loss or tie, although all five measured files selected
an experimental candidate. Tests cover the actual fallback and tie paths.

## Implementation and correctness

[`research/relational_hybrid.py`](../research/relational_hybrid.py) calls the
unchanged public AHPS compressor once and recovers the actual winning plan
from its serialized archive. It regenerates fragments from original file bytes,
rebases executable origins by each fragment's displacement, and replaces only
the complete declared exception table. Both candidates retain identical outside
descriptors and boundaries. Existing predictor/scanner history restarts at new
boundaries; old transformed residuals are never sliced or reused.

Both aligned candidates use independent `AHRH1` framing. The research word step
subtracts the previous record's corresponding uint32 modulo 2^32, preserves the
first record and then uses existing SPLIT(12). The word arithmetic runs in Python;
existing transforms use the frozen native engine. Production opcodes and ABI
are unchanged. Use the research decoder for selected outputs; it accepts AHRH1
and ordinary AHPS v5 fallbacks. Production AHPS does not decode AHRH1.

The decoder validates the XZ stream, header, every descriptor, output/segment
limits and actual expansion before invoking native transforms, then checks the
restored CRC. It does no PE parsing, planning or candidate search. Source and
table caps are 128 MiB and 8 MiB; the detailed wire contract and rejection rules
are in the frozen protocol.

**136 targeted regression tests passed in 5.139 seconds, with no failures or
skips**, including 27 independent new tests for wire bytes, arithmetic, crossing
boundaries, address wrap, immutable plans, native/Python parity, baseline reuse,
selection, malformed archives and bounded decompression. The full repository
suite was not rerun. Synthetic harness checks also exercised available and
unavailable tables, dependent failures, and a complete summary before freezing.

The measured run completed **15 archive encodes and 20 output round trips**,
each with exact complete-source length and SHA-256. The five selected outputs
are copies of already measured winning archives, not five additional encodes.
Actual serialized plans, candidate hashes, selected byte identity and the
never-larger-than-original bound were verified for every file.

## Resource use and timing scope

Tests, preparation, controllers and all workers verified BELOW_NORMAL priority
(`0x4000`). Workers ran serially under the shared benchmark lock with **300
seconds per file, 900 seconds per batch and 2 GiB aggregate Job committed
memory**, no breakaway and verified owned-tree cleanup. No worker failed or
timed out; no limit was extended and no case was retried. The batch took
**267.219 seconds**. Node's complete worker took **226.547 seconds**.

The measurement host was in interactive use. These are single-pass diagnostic observations:

| File | Baseline encode API, seconds | Hybrid measured encode components, seconds |
| --- | ---: | ---: |
| 7zip-cli.exe | 0.910 | 1.287 |
| chrome.exe | 9.852 | 14.204 |
| git-cli.exe | 9.784 | 15.487 |
| node.exe | 144.799 | 216.045 |
| vlc.exe | 2.153 | 2.865 |

The hybrid column includes baseline encoding, baseline-plan recovery, both
candidate plan builds/encodes and selection. It excludes metadata-only
serialization, benchmark verification and artifact I/O; it is not a separate
timed invocation of the convenience encoder. The additional two complete
candidate encodes are real search work, even when the selected archive is tiny
or falls back to AHPS. Selection time alone is not hybrid encoding time.

The baseline uses the unchanged production decoder; candidates and selected
outputs use the research decoder, with the actual API recorded. Decode times
and Job memory records remain in JSON. PC activity, shared process state and
different decoder implementations prevent stable speed or per-method memory
claims from these observations.

## Evidence and decision

Immutable sources, full-file snapshots, 20 archives and 20 restored files,
phase/Job logs and freeze records remain locally under
`experiments/relational-hybrid-20261103/`. The preflight log is
`experiments/relational-hybrid-preflight-20261103/tests.log`.

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `6b18274009bcbf4f3854353e79b4aa71e730493ae700c1a13ef8eedb91dff684` |
| Freeze manifest | `de53006cac1217cc3ea2c5a6d5e8effea94bcce0b1019247ae222cd105e81570` |
| Preflight log | `f6c47894ec2f76da4106f0c7c6bbcf1ea8918366146f5855513cb4ca6603065d` |
| Hybrid codec | `620d83c5de3c73298c0b35e95b79e05dcbfd5aca8b3d719e15020c9199f71422` |
| Comparison harness | `af26584c269d7a0104c10f12acae2c47fbb0b34f7472ad093c8fa2d0885f0f22` |
| New tests | `d28e02e92b613adc61b9a217ab094aef20696c0e60e47d0703f9884f82597a34` |

Retain this working prototype and evidence on the research branch. The result
supports selective structured-region transforms, with a modest additional
word-delta benefit; it does not justify production adoption or a general
compression claim. All five inputs are reused development executables. Broader
independent validation and a separate assessment of search/decode costs would
be needed before promotion. No predictor retuning, additional corpus run or
production change followed these measurements.
