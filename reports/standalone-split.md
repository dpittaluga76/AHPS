# Standalone SPLIT on a fixed typed region

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Task 5.

All 22 complete-file methods and 63 local method/window combinations completed and restored exactly.

The [preregistered protocol](../docs/standalone-split-experiment.md) uses the existing development MR file. Its parsed DICOM metadata declares 19 frames of 512 × 512 unsigned 16-bit little-endian pixels. Only its declared 9,961,472-byte pixel region, starting at offset 9092, receives the manual transform. Metadata is RAW. Both segments and every program byte are included in each complete AHPS v5 archive.

The original task-1 reference is used (commit `03e4de4aed039064479e8b31ace96bdf6a5f4644`, ABI 206). All methods use one solid XZ stream at preset 9 plus EXTREME, CRC64, and a 64 MiB dictionary. The automatic AHPS reference keeps its actual selected segments and framing.

## Complete-file results

| Method | Archive bytes | Versus fixed RAW | Encode process s | Decode process s | Encode peak MiB | Decode peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| raw | 2,753,008 | +0.00% | 3.813 | 0.312 | 203.84 | 54.01 |
| delta1-2 | 2,612,320 | +5.11% | 3.844 | 0.313 | 203.75 | 54.31 |
| delta2-2 | 2,683,380 | +2.53% | 4.016 | 0.312 | 203.88 | 54.41 |
| split-2 | 2,582,552 | +6.19% | 3.203 | 0.313 | 203.71 | 54.26 |
| delta1-split-2 | 2,473,804 | +10.14% | 3.391 | 0.313 | 203.42 | 53.55 |
| delta2-split-2 | 2,530,308 | +8.09% | 3.156 | 0.313 | 203.58 | 53.65 |
| delta1-4 | 2,939,548 | -6.78% | 3.860 | 0.312 | 204.43 | 54.98 |
| delta2-4 | 3,271,868 | -18.85% | 3.703 | 0.359 | 205.02 | 55.58 |
| split-4 | 2,734,972 | +0.66% | 3.172 | 0.328 | 203.99 | 54.59 |
| delta1-split-4 | 2,849,596 | -3.51% | 3.078 | 0.344 | 204.21 | 54.28 |
| delta2-split-4 | 3,062,236 | -11.23% | 2.953 | 0.344 | 204.64 | 54.64 |
| delta1-8 | 3,078,924 | -11.84% | 3.860 | 0.343 | 204.71 | 55.19 |
| delta2-8 | 3,507,156 | -27.39% | 3.828 | 0.344 | 205.53 | 56.05 |
| split-8 | 2,838,112 | -3.09% | 2.890 | 0.344 | 204.19 | 54.76 |
| delta1-split-8 | 3,011,900 | -9.40% | 2.750 | 0.343 | 204.50 | 54.56 |
| delta2-split-8 | 3,247,484 | -17.96% | 2.625 | 0.344 | 204.93 | 55.00 |
| delta1-16 | 3,228,964 | -17.29% | 4.141 | 0.359 | 205.00 | 55.53 |
| delta2-16 | 3,731,408 | -35.54% | 3.813 | 0.344 | 205.94 | 56.45 |
| split-16 | 2,921,748 | -6.13% | 2.797 | 0.344 | 204.38 | 54.95 |
| delta1-split-16 | 3,144,032 | -14.20% | 2.657 | 0.343 | 204.76 | 54.70 |
| delta2-split-16 | 3,410,776 | -23.89% | 2.609 | 0.344 | 205.19 | 55.24 |
| existing-ahps | 2,481,576 | +9.86% | 7.484 | 0.344 | 206.81 | 53.56 |

Positive size percentages mean smaller than the fixed two-segment RAW control. Plain DELTA1/DELTA2 parameters are `(stride,0,0,0)`; modal-bias and automatic choices are covered separately by the unmodified AHPS reference.

## Comparison of the candidate families

| Family | Best method | Complete bytes | Standalone SPLIT gain versus this family |
| --- | --- | ---: | ---: |
| raw | raw | 2,753,008 | +6.19% |
| plain_delta | delta1-2 | 2,612,320 | +1.14% |
| standalone_split | split-2 | 2,582,552 | +0.00% |
| delta_split | delta1-split-2 | 2,473,804 | -4.40% |
| existing_ahps | existing-ahps | 2,481,576 | -4.07% |

Standalone SPLIT does not beat the best existing delta/chain/automatic alternative on this MR parent. This screen therefore supplies no basis to add standalone SPLIT to automatic discovery yet. Task 6 is deferred for lack of a useful incremental result on the declared input.

## Local trials

Three fixed 64 KiB windows begin at frames 0, 9 and 18. Each tests all 21 manual candidates. Sizes below are raw LZMA2 payload bytes without AHPS/XZ framing, at preset 1 (existing local refinement setting) and preset 9 plus EXTREME. Complete-file rankings above remain primary.

| Window | Best local preset 1 | Bytes | Best local preset 9 extreme | Bytes |
| --- | --- | ---: | --- | ---: |
| frame-0-first64k | split-2 | 12,825 | split-2 | 11,858 |
| frame-9-first64k | split-2 | 11,752 | split-2 | 10,828 |
| frame-18-first64k | split-2 | 13,156 | split-2 | 12,173 |

All local results and the unchanged C MDL estimates are retained in JSON, including losing strides and chains. These are manual fixed-candidate trials, not a change to the planner.

Standalone SPLIT(2) wins both local preset rankings on every declared window, while the complete solid file favors DELTA1(2) → SPLIT(2). This observed ranking reversal warns against adding a candidate solely on these short-window results. The unchanged C estimate favors RAW over standalone SPLIT on all three windows even though actual raw LZMA2 costs favor SPLIT; its scores are heuristic estimates, not archive sizes.

The best manual DELTA1(2) → SPLIT(2) archive is 7,772 bytes (0.31%) smaller than automatic AHPS. Its known typed boundary and uniform program differ from the automatic plan; this gain does not require a new opcode and cannot be attributed to boundaries alone.

## Verification, costs and limitations

Preflight passed 210 native/Python short/tail agreement and inverse cases. Post-run audit verified 22 actual archives and restored files against their recorded sizes/hashes and the parent hash. Every successful phase had a zero exit, verified Job cap and cleaned process tree.

The revised monitor also passed all nine synthetic resource checks after the serial corpus run, including aggregate-memory denial and timeout cleanup of the complete process tree.

The clean series took 86.641 seconds. The initial attempt stopped after 8.390 seconds because the old monitor treated a briefly lingering Windows console helper as a live-descendant error after the encoder exited zero. Its entire cohort is excluded from the comparison and preserved in the JSON. The isolated runner was revised to wait for the whole tree under the same deadline. The full unchanged cohort was then rerun; the original 600-second deadline was retained across the infrastructure correction.

Elapsed from the initial attempt's first worker through the clean local-trial completion, including the correction interval: 149.031 seconds of the original 600-second ceiling.

Encode/decode process durations include startup, imports, reference-integrity checks and required I/O; inner operation timings are separate in JSON. Peak MiB columns use the retained kernel lifetime peak working set of the actual Python phase worker. Console helpers are included in aggregate Job accounting, recorded separately. One clean pass gives diagnostic costs, without a stable speed or memory-ratio claim.

This is one previously used uint16 development image file. It supplies no float-tensor, executable, other-image or corpus-wide result. No sealed file or reserve was read. No production source, DLL, default, format or candidate-discovery change was made. Further PAQ execution was skipped at the maintainer’s request.

## Reproducibility

- Machine-readable evidence and exact identities (local record `standalone-split-20261103.json`).
- Original parent SHA-256: `68637ed52e3e4860174ed2dc0840ac77d5f1a60abbcb13770d5754e3774d53e6`.
- Local evidence, original and revised plans, the preserved infrastructure attempt, complete archives/restored files and isolated runner are under `experiments/split-20261103/`.
- Typed-region interpretation: [DICOM transfer syntax](https://dicom.nema.org/medical/dicom/2024e/output/chtml/part05/chapter_A.html) and [implicit-VR element layout](https://dicom.nema.org/medical/dicom/2024e/output/chtml/part05/sect_7.1.3.html).
