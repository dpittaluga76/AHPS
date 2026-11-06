# Independent AHRC backend: first complete-file comparison

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. A working **LZMA2-free backend** is implemented on the separate
branch `codex/independent-compression-backend`, from checkpoint `3788ed9`.
It performs its own LZ77 matching, probability modeling and binary arithmetic
coding. This first version **does not beat frozen AHPS**: its selected archives
are larger on all five development files, by **24.316842% overall**.

The [frozen design](../docs/independent-backend-experiment.md),
complete measurement record (local record `independent-backend-20261103.json`), and
[independent audit](independent-backend-audit-20261103.md) retain the successful
restorations and the unfavorable size results. No post-result tuning or retry
was performed. Production AHPS and all earlier research codecs remain unchanged.

## Complete-file results

All archive sizes include the 52-byte AHRC header, backend framing, transform
descriptors represented by the header, and checksums. AHPS is the unchanged
preset-6, block-4096, FAST_CHUNKS, native-ABI-206 baseline. Every file is complete.

| File | Frozen AHPS bytes | Selected AHRC bytes | AHRC larger | Selected representation |
| --- | ---: | ---: | ---: | --- |
| 7zip-cli.exe | 181,776 | 210,390 | 15.741352% | Table byte delta |
| chrome.exe | 1,628,332 | 1,880,606 | 15.492786% | Table byte delta |
| git-cli.exe | 1,473,840 | 1,671,411 | 13.405186% | Table word delta |
| node.exe | 21,768,312 | 27,418,123 | 25.954291% | Table byte delta |
| vlc.exe | 301,752 | 338,777 | 12.270010% | Table word delta |
| Sum of these five files | 25,354,012 | 31,519,307 | 24.316842% | Three byte, two word |

AHRC adds **6,165,295 bytes** relative to AHPS on the complete set. Source sizes
are 575,488; 4,522,648; 4,055,416; 91,426,304; and 1,046,424 bytes, totaling
**101,626,280 bytes**. Every original/snapshot matched the development manifest,
and all five freshly produced AHPS archives reproduced their earlier frozen
hashes exactly, including Node. No source was replaced with a sample or table.

All twenty independent representation trials used **coded mode**, as did the
five selected copies. None of these measured outcomes used stored mode or an
AHPS fallback. Stored mode is covered by the preflight tests.

## What the representations contribute

Here `raw` means that the new backend compresses the original bytes without a
pretransform; it does not mean an uncompressed archive. Every column below uses
the same independent backend and complete-file framing.

| File | Raw | Whole-file EXE_SPLIT | Table byte delta | Table word delta |
| --- | ---: | ---: | ---: | ---: |
| 7zip-cli.exe | 250,553 | 222,495 | 210,390 | 210,836 |
| chrome.exe | 2,093,652 | 1,904,203 | 1,880,606 | 1,881,786 |
| git-cli.exe | 1,920,058 | 1,710,502 | 1,673,383 | 1,671,411 |
| node.exe | 28,941,038 | 27,646,567 | 27,418,123 | 27,421,955 |
| vlc.exe | 383,218 | 356,714 | 339,446 | 338,777 |
| Sum | 33,588,519 | 31,840,481 | 31,521,948 | 31,524,765 |

Representation selection saves **2,069,212 bytes (6.160474%)** against feeding
the same backend unchanged source bytes. These transforms help this backend,
but do not close its gap to AHPS.

Byte and word table modes use identical boundaries and outside transforms.
Word delta saves 1,972 bytes on Git and 669 bytes on VLC, but costs 446 bytes
on 7-Zip, 1,180 on Chrome and 3,832 on Node. Forcing word delta over byte delta
would add 2,817 bytes across the set; selecting between them saves 2,641 bytes
relative to always choosing the byte table mode. This differs from the earlier
LZMA2-backed rankings and reinforces that a representation's value depends on
its backend and complete-stream context.

The comparison against original AHPS changes both backend and planning policy:
AHPS retains its adaptive planner, while this first independent codec tries
four fixed representations. It does not isolate the entire size deficit to one
matcher, probability model or transform decision.

## What is independent

[`ahrc_backend.c`](../research/ahrc_backend.c) implements a continuous 8 MiB
LZ77 history, bounded 32-probe hash-chain search, greedy literal/match parsing,
one remembered match distance, previous-byte literal contexts and separate
adaptive match/length/distance models. Its binary arithmetic coder and decoder
are locally implemented, with bounded reads/writes and explicit bit termination.
This uses established algorithmic ideas; no novelty or breakthrough is claimed.

[`independent_backend.py`](../research/independent_backend.py) writes a new
`AHRC1` format. It uses only direct calls to the frozen AHPS native library for
reversible EXE_SPLIT, byte delta and lane splitting. Word delta is implemented
in the new C DLL. It does not call the old Python planner, recover an LZMA-chosen
plan, or use another compressor for candidate encoding or decoding.

Stored-payload fallback copies transformed bytes into AHRC when coding would
not reduce their length. Selection considers only the four independent AHRC
variants, with stable raw-first ties. Every variant is bounded by source size
plus 52 bytes; there is no guarantee of matching AHPS's size. No selected output
can silently become an AHPS/XZ archive through the encoder's fallback path.

Independence evidence includes:

- A fresh isolated process successfully encoded and decoded all variants and
  automatic selection while imports of LZMA, other compression libraries and
  old Python codecs/planners were denied.
- **75 guarded candidate API scopes** completed without attempting prohibited
  compression or old-planner entry points. These cover encode/selection,
  decode and metadata inspection; a synthetic harness test also caught an
  intentionally swallowed forbidden call.
- The separate DLL's inspected imports resolve to Windows CRT/kernel modules.
  Compiler, source, DLL, build command and resolved dependency identities are
  recorded. Rebuilding produced an identical DLL hash.
- Every independent output has AHRC framing and uses the custom bitstream;
  there is no XZ wrapper or embedded AHPS archive.

The benchmark still invokes LZMA2 **inside the separate AHPS reference phase**.
That is required to measure the requested baseline. The independent encoder
and decoder do not use it; the comparison worker is not claimed to be a process
in which the baseline's libraries were never loaded.

## Validation and resource use

The targeted preflight passed **135 tests in 9.342 seconds, no failures or
skips**, including 22 new independent-backend tests. Those include a separate
Python arithmetic/token reference, hardcoded wire fixtures, native/reference
cross-decoding, overlapping matches, the exact history-window boundary, output
canaries, capacity failures, malformed streams, CRCs, transform origins, stable
selection and import isolation. The full repository suite was not rerun.

The measured run completed **25 encode operations and 30 output round trips**:
five AHPS encodes, twenty independent representation trials, and five selected
copies. Each output restored the complete source length and SHA-256. Selected
copies are not extra encodes. All table bounds and stored/coded choices were
recorded, and the stable minimum was verified against actual archive bytes.

Tests, compilation, preparation, controllers, workers and audit hashing used
verified Windows BELOW_NORMAL priority (`0x4000`). Workers ran serially under
the shared benchmark lock, with **300 seconds per file, 900 seconds per batch,
2 GiB aggregate Job committed memory**, no breakaway and verified tree cleanup.
The batch finished in **181.109 seconds**; Node's worker took **158.469 seconds**.
No worker timed out, failed or required a retry or limit extension.

The measurement host was in interactive use. These are single-pass diagnostic times:

| File | AHPS encode API, seconds | Four AHRC trial APIs + selection, seconds | AHPS decode, seconds | Selected AHRC decode, seconds |
| --- | ---: | ---: | ---: | ---: |
| 7zip-cli.exe | 0.494 | 0.158 | 0.0102 | 0.0236 |
| chrome.exe | 4.986 | 2.893 | 0.0781 | 0.2117 |
| git-cli.exe | 4.761 | 2.185 | 0.0700 | 0.1817 |
| node.exe | 69.512 | 69.975 | 1.0364 | 3.1955 |
| vlc.exe | 0.803 | 0.276 | 0.0145 | 0.0383 |

The AHRC encoding column sums all eligible trial API calls plus selection and
excludes the AHPS reference. Work performed inside those APIs, including their
metadata/hash construction, is included. External benchmark verification,
artifact I/O and summary serialization are excluded. This is not a separately
timed invocation of the automatic-selection API. User activity, shared process
state and single observations do not establish stable throughput; shared Job
peaks are not per-codec peak-memory comparisons.

## Evidence and decision

The build used GCC 16.2.0 with the recorded portable optimization/warning flags.
The new DLL uses backend ABI 1; the unchanged transform DLL uses ABI 206.
Source snapshots, complete inputs, 30 archives/restored outputs, build evidence,
phase logs and Job records remain under `experiments/independent-backend-20261103/`.
The test log is `experiments/independent-backend-preflight-20261103/tests.log`.

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `aa7685b750b2e4924f7043418fc0c596ae7bb4a75ce60999bf63d007be28a3e1` |
| Freeze manifest | `702a956b1d84245764659df1d96e4bda9b850ba3184ea90a5f6f6063109000d7` |
| Preflight log | `324c58a5a108d5ddd174c2c22ee19c4531e2f70aa35686e05fa072437ac39f04` |
| C backend | `053a2e83d6c9101cff4ca75da2c685fbd30b1e7c46c4e81576520a3e7319ea0a` |
| Backend DLL | `2677ecc67598595d6d18dc5ced8a54596d084f32b7599fca257e8bb4e2165c30` |
| Build manifest | `618b2a0c360469dbd207200e202bf70a5678bf2e4c10b26b7b39900bd6bfb45d` |
| Python API | `0904b928c8683332b36ba60aff46049266a3e2ed18f15030bb9238c7e4757c8f` |
| Comparison harness | `2313a91fbc2c38ea2f38dede7be6a989888ddb8a749112bd0204c1841c786d15` |
| New tests | `d1661baa63a40c0b8b5f1555ff96a1097384d76545618f9320dd47cdb748e258` |

Retain this as a tested independent-backend starting point on its own branch.
It fulfills backend independence and complete-file correctness on this cohort,
but its size results favor AHPS on every file. The earlier 0.090% hybrid gain
belonged to the LZMA2-backed experiment and does not transfer to this backend.
Any next parser/model improvement needs a new frozen comparison; no parameter
retuning, production promotion or general compression claim follows here.
