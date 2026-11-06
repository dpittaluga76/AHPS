# Same-boundary exception-table results

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. On `codex/relational-compression`, word delta beats the best
tested existing control on **four of five development-folder tables**, plus the
reused Blender reference. Chrome favors AHPS byte delta. This supports a
conditional word-arithmetic candidate for this table class, not a whole-file
compression claim or evidence for cross-field prediction.

The [frozen protocol](../docs/same-boundary-table-experiment.md),
machine-readable results (local record `same-boundary-tables-20261103.json`), and
[independent audit](same-boundary-tables-audit-20261103.md) retain every result.
Production AHPS, its native library, the AHRL core, formats and planners are
unchanged from research checkpoint `de5f78c`.

## Complete inputs and archive sizes

Each input is the complete declared x64 exception directory, excluding section
padding. Its parent matched the development manifest before and after extraction.
Every method receives the identical table starting at zero. All sizes below
include the complete archive, headers, models and XZ framing, using preset 6 and
CRC64. Lower is better. D1 and D2 are existing AHPS byte deltas at stride 12,
followed by SPLIT(12); word delta uses three little-endian uint32 fields.

| Table | Source bytes | Bare XZ | AHPS RAW | SPLIT12 | D1 + SPLIT12 | D2 + SPLIT12 | Word delta | Automatic AHPS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Blender (reused reference) | 2,484,204 | 855,704 | 860,096 | 579,060 | 535,212 | 602,324 | 511,972 | 535,212 |
| 7zip-cli.exe | 30,036 | 9,332 | 9,348 | 6,956 | 5,892 | 6,780 | 5,780 | 5,892 |
| chrome.exe | 89,580 | 34,192 | 33,608 | 28,320 | 27,796 | 30,288 | 28,016 | 27,796 |
| git-cli.exe | 85,260 | 29,080 | 29,104 | 20,912 | 18,308 | 19,588 | 16,908 | 18,308 |
| node.exe | 830,904 | 273,740 | 274,924 | 215,408 | 204,648 | 223,840 | 203,464 | 204,648 |
| vlc.exe | 44,748 | 16,796 | 16,820 | 13,428 | 11,072 | 12,284 | 10,224 | 11,072 |

Automatic AHPS selected **one whole-table D1(12,0) -> SPLIT(12) segment on all
six inputs**. Its archives are byte-identical to the fixed D1 control. Thus no
missed fixed-program selection or internal-boundary improvement was demonstrated
on these extracted tables. This says nothing yet about finding the regions
inside a complete executable. The tested controls do not exhaust every possible
AHPS program.

| Table | Word-delta saving against best tested existing control | Bytes saved |
| --- | ---: | ---: |
| Blender (reused reference) | 4.34% | 23,240 |
| 7zip-cli.exe | 1.90% | 112 |
| chrome.exe | -0.79% (larger) | -220 |
| git-cli.exe | 7.65% | 1,400 |
| node.exe | 0.58% | 1,184 |
| vlc.exe | 7.66% | 848 |

These percentages use compressed table sizes as their denominators. They must
not be applied to complete executables. Blender reproduces the earlier result
and is not a new independent success; the other five are development follow-ups,
not sealed final evaluation.

## Separate arithmetic diagnostic

The actual residual from each validated fixed archive was compressed alone with
the same XZ settings, decompressed, and reversed to the original table. These
streams omit transform metadata and require external instructions to decode;
they are diagnostics, not standalone codecs.

| Table | RAW residual | SPLIT12 residual | D1 residual | D2 residual | Word residual | Word saving vs D1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Blender (reused reference) | 855,704 | 579,048 | 535,600 | 602,148 | 512,004 | 4.41% |
| 7zip-cli.exe | 9,332 | 6,940 | 5,872 | 6,752 | 5,760 | 1.91% |
| chrome.exe | 34,192 | 28,308 | 27,716 | 30,296 | 27,992 | -1.00% |
| git-cli.exe | 29,080 | 20,900 | 18,276 | 19,548 | 16,908 | 7.49% |
| node.exe | 273,740 | 215,292 | 204,428 | 223,904 | 203,804 | 0.31% |
| vlc.exe | 16,796 | 13,416 | 11,032 | 12,252 | 10,200 | 7.54% |

D1 and word delta have the same first-record passthrough and final 12-lane
permutation. Their difference is subtraction modulo 256 per byte versus modulo
2^32 per field, including carries and borrows. The residual ranking agrees with
the complete-archive ranking on all six tables, so the favorable results survive
removing the different container metadata. D2 has a different predictor order
and two-record warmup; it is an existing rival, not the arithmetic control.

Compression is stateful: a header can change the coding of following bytes.
The difference between a full archive and its residual-only XZ is not an additive
metadata cost. Node's modest residual saving (624 bytes) and Chrome's regression
especially argue against applying word delta unconditionally.

## Verification and resource limits

All six inputs were available. **42/42 complete archive round trips and 30/30
residual checks passed**, each with exact restored length and SHA-256. The
targeted preflight passed **106 tests in 8.307 seconds, with no failures or
skips**, including 17 new tests for fixed programs, arithmetic, independent lane
ordering/inverse, native/Python parity, malformed archives and bounded expansion.
The earlier full repository suite was not rerun for this harness-only change.

Controllers, tests and workers verified below-normal priority (`0x4000`). The
six workers ran serially under the shared benchmark lock, with 120 seconds per
table, a 600-second batch limit, 2 GiB aggregate committed memory per Job, no
breakaway, and verified tree cleanup. All workers completed without a timeout
or retry; the measured batch took **29.297 seconds**. The maintainer was using the PC:
single-pass timings are diagnostic, and Job peaks do not compare per-method
memory. The earlier whole-file Node timeout remains a separate result.

Frozen sources, table bytes, archives, residuals, restored outputs and logs are
retained locally under `experiments/same-boundary-tables-20261103/`. The test log
is `experiments/same-boundary-preflight-20261103/tests.log`. Their identities are:

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `350e475de9f7d9da68e36a949f04c9cbb0cd498de395e3f6055fa9494021239c` |
| Freeze manifest | `5ddef018b16235b7c12f4e31a8067f51a8b18495ea99da154c01270d28464581` |
| Preflight log | `7e8940b0ac02f14c1eb0204cb4cf78700b2bc57a8b76dda9835e2322dddfc9a4` |
| Comparison harness | `8286eb55981c28f77b3740f0685fa44e2f4bb3d3b520564ed67c5df6c864c159` |
| New tests | `f1e459ef69244ad01bc2f9f2564cc164affa35df362f691ea0f03fd5940172a4` |

## Next justified experiment

Test selective word delta inside a complete-file research hybrid: retain an
original AHPS baseline, an existing-program control with the same explicit
table boundaries, and the word-delta candidate at those boundaries. Preserve
executable-transform offsets when splitting the file. Keep one solid XZ stream,
count all versioned metadata, verify full-file restoration and
choose using complete archive cost. Preserve the original AHPS archive as the
fallback, so Chrome and any loss from solid-stream interactions can reject the
candidate. The modest absolute table gains may vanish at whole-file scale.

That follow-up requires its own frozen protocol and implementation. This run
does not adopt word delta in production, add an opcode, implement region
discovery, or establish a cross-field breakthrough.
