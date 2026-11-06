# AHPS Relational: complete development-folder screen

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. **AHPS Relational (AHRL)** is the prototype's working name.
The requested directory contains five executable files. All five complete inputs
were included; four finished all comparisons, while Node's worker timed out.

On the four completed comparisons, AHRL AUTO selected RAW and produced archives
**6.86% to 15.45% larger than the frozen production AHPS implementation**.
There was no measured representation gain over the identically framed RAW
control. The current blind fixed-layout prototype is not competitive with AHPS
on these four executables. Node has no completed AUTO/AHPS size comparison.

## Complete archive sizes

| File | Solid XZ bytes | AHRL RAW bytes | AHRL AUTO bytes | AHPS bytes | AUTO larger than AHPS |
| --- | ---: | ---: | ---: | ---: | ---: |
| 7zip-cli.exe | 209,852 | 209,864 | 209,864 | 181,776 | 15.45% |
| chrome.exe | 1,778,352 | 1,777,948 | 1,777,948 | 1,628,332 | 9.19% |
| git-cli.exe | 1,665,804 | 1,666,048 | 1,666,048 | 1,473,840 | 13.04% |
| node.exe | 22,903,204 | 23,663,876 | Timed out | Not started | — |
| vlc.exe | 322,360 | 322,456 | 322,456 | 301,752 | 6.86% |

These include every header, frame and model byte. All methods use preset 6 and
CRC64 XZ; production AHPS uses native ABI 206, block size 4096 and explicit
`FAST_CHUNKS`. This is not the historical preset-9/BCJ2 comparison.

AUTO and matched RAW have equal sizes on every completed file, and every AUTO
frame is stored RAW. Small differences between these framed RAW archives and
bare XZ, including Chrome's slightly smaller framed archive, are not gains from
prediction: AUTO selected no transformed representation on those files.

There is no five-file aggregate compression ratio: the Node AUTO/AHPS results
are missing. Do not infer a corpus-wide win/loss rate from the successful subset.

## Scope and timeout

The source sizes were 575,488; 4,522,648; 4,055,416; 91,426,304; and 1,046,424
bytes respectively, totaling 101,626,280 bytes. Each original matched the saved
development manifest before and after snapshotting. No source was replaced
with a sample or typed region. No sealed input, reserve or other directory was
read for this comparison.

The measurement host was in interactive use. Every measured worker ran at verified
`BELOW_NORMAL_PRIORITY_CLASS` (0x4000), with checks before encode/decode and
after completed methods. Runs were serial under the shared benchmark lock and
a 2 GiB aggregate committed-memory Job cap. API times and Job-wide resource
records are diagnostic, not stable speed comparisons or per-codec memory peaks.

The fixed limit was **300 seconds per file**, shared by its four methods,
within a 900-second batch budget. Node completed solid XZ and AHRL RAW, including
exact restoration of all 91,426,304 bytes. Its AUTO encode began about 127.6
seconds into that worker; the worker was stopped at 300.047 seconds, roughly
172.4 seconds into AUTO encoding. AUTO did not receive a separate five-minute
budget. The subsequent AHPS case was unstarted. The retained failure is a
timeout under these conditions, not corruption or a completed size result.

VLC ran afterward and completed. The batch lasted 380.000 seconds. No case was
retried, extended or silently discarded, and all owned worker trees were cleaned
up. The machine-readable verdict remains `incomplete_or_failed`.

## Adapter and validation

The original record codec was unchanged. A separate
[`research/relational_file.py`](../research/relational_file.py) adapter makes it
possible to test complete files above the record codec's 8 MiB cap. It uses
three little-endian uint32 fields everywhere, 8,388,600-byte aligned chunks,
explicit original lengths and zero padding only for the final partial record.
Its independent `AHRF` framing contains the existing `AHRL` archives. The adapter
caps source files at 128 MiB and validates all frame sizes, layouts, padding,
inner checks and the restored whole-file CRC.

This is a **blind layout applied to executables**, not automatic detection of
real records. Node's RAW control spans eleven chunks; all four smaller files
use one each. Chunk resets and framing are included in the whole-file result.
The matched RAW control distinguishes representation gains from those effects.
The earlier gain on a known exception table does not imply a gain when treating
every byte of an executable as the same record type.

**261 regression tests passed**, with no skips, in 69.369 seconds at verified
below-normal priority. This includes 18 new adapter tests for boundary/tail
preservation, independently constructed wire fixtures, RAW/AUTO bounds, invalid
dimensions, frame counts, checksums, padding, trailing data and actual bounded
inner expansion. **18 of 20 planned comparison cells completed and restored
the full source length and SHA-256.** The other two are Node AUTO (interrupted)
and Node AHPS (unstarted); neither is counted as a successful round trip.

Production AHPS Python/C/DLL, its format/defaults, and the underlying AHRL model
implementation retain their pre-run identities. No model or layout was tuned
using these outcomes, and no production change follows from this screen.

## Evidence

- [Frozen protocol](../docs/relational-corpus-experiment.md).
- All 20 planned outcomes, identities and resource records (local record `relational-corpus-20261103.json`).
- [Independent evidence audit](relational-corpus-audit-20261103.md).
- [Harness](../research/compare_relational_corpus.py) and
  [adapter tests](../tests/test_relational_file.py).
- Local immutable snapshots, complete archives/restored files, timeout evidence
  and logs: `experiments/relational-corpus-20261103/`.
- Full regression log: `experiments/relational-corpus-preflight-20261103/tests.log`,
  SHA-256 `94c561403cb5a4b07050428ee7d981c85d80e32d75df134b20c46c3343537c20`.

Keep AHPS as the production codec. AHRL remains a research prototype for testing
structured representations; this completed screen does not establish a general
executable-compression improvement, and the full five-file comparison is incomplete.
