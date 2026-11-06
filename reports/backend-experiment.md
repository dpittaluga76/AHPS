# Representation and backend pilot

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026.

The pilot stopped under its saved protocol: **Probe extrapolation clearly exceeds saved runtime limits**. The main comparison is incomplete, so backend and transform hypothesis gates are not evaluated.

Execution followed the [saved plan](../docs/representation-backend-experiment.md). The original frozen planner and native DLL were used, not the current POLY2-deduplicated sources. Production code, format and defaults were unchanged by this task.

## Completion and limits

Timed elapsed: **237.250 s** of 2,700 s. Main cells: **0/35 successful**; probe cells: **3/3 successful**. No retry or replacement input was used.

The limits remained 60 s per preparation, 300 s per complete cell, 900 s per batch, and 8 GiB aggregate committed memory for the worker tree. Acquisition, harness review and synthetic enforcement checks preceded the timed clock. One worker ran at a time under the repository benchmark lock.

Windows Job limits were read back before a suspended worker resumed. Breakaway was disabled, and timeout cleanup covered all descendants. The pre-run validation passed all nine checks, including denial of the second child allocation under an aggregate cap and complete timeout-tree termination. Peak resident memory below is sampled aggregate working set at a nominal 20 ms interval, not the committed-memory cap or an exact simultaneous kernel peak. Raw kernel counters and retained process peaks are preserved in JSON.

## Probe

Input: 256 KiB from Blender at byte offset 15,728,640, independently planned. B is RAW-container PAQ; D is transformed-container PAQ; both use v217 `-8 -forcebinary`. Complete archives include the physical 136-byte experimental wrapper.

| Probe | Outcome | Archive bytes | Backend encode s | Backend decode s | Complete worker-tree s | Encode resident MiB | Decode resident MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B | verified | 60,462 | 39.287 | 39.658 | 79.218 | 2235.11 | 2234.61 |
| D | verified | 58,226 | 38.591 | 39.844 | 78.718 | 2234.62 | 2234.81 |
| B-duplicate | verified | 60,462 | 38.674 | 39.640 | 78.593 | 2234.00 | 2234.07 |

Probe B complete-archive determinism: **True**. Each completed decode wrote and verified fresh output bytes.

On this single 256 KiB probe, the transformed PAQ archive was 3.70% smaller than RAW-container PAQ (2,236 bytes). No XZ probe cell was planned or run, so this is not a backend comparison or a main-matrix transfer result.

The preregistered 8× diagnostic estimates were 633.744 s, 629.744 s, 628.744 s per 2 MiB cell. The parent estimate was 2594.976 s against 900 s; the five-parent estimate was 12974.880 s against 2462.766 s remaining.

This estimate applies the probe’s forced-binary timing to four PAQ cells per parent and omits LZMA costs. It also scales fixed startup costs. It is a feasibility screen, not a forecast or a claim about whole-file performance.

## Preparation and representation coverage

| Input | Prep tree s | Planner s | Selected changed residual bytes | Active segment bytes | RAW and selected identical |
| --- | ---: | ---: | ---: | ---: | --- |
| probe | 0.609 | 0.201 | 39,507 | 262,144 | False |

The probe selected one EXE32 segment (opcode 8) covering all 262,144 bytes, with parameters `00000000`. Changed bytes compare residual bytes with the original, excluding framing. Full plans, opcode coverage, parent hashes, slice hashes and both frozen container hashes are in the JSON. All main slices are below the 16 MiB chunk threshold.

## All planned main outcomes

A/B use the RAW container with XZ/PAQ; C/D use the frozen transformed container with XZ/PAQ. E/F/G compress naked originals with XZ, forced-binary PAQ and automatic PAQ. A–D include the wrapper; native headers count in every cell.

| Parent | A | B | C | D | E | F | G |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| blender | not_run | not_run | not_run | not_run | not_run | not_run | not_run |
| node | not_run | not_run | not_run | not_run | not_run | not_run | not_run |
| x-ray | not_run | not_run | not_run | not_run | not_run | not_run | not_run |
| mr | not_run | not_run | not_run | not_run | not_run | not_run | not_run |
| webster | not_run | not_run | not_run | not_run | not_run | not_run | not_run |

Values are complete archive bytes. Every `not_run` cell remains in the 35-cell denominator; it is neither a success nor a measured loss.

## Interpretation

There is no complete main-matrix evidence for either executable parent, either structured-data parent, or the Webster negative control. The Blender probe cannot substitute for those five fixed 2 MiB slices. Backend gain, transfer gates, negative transfer and interaction are therefore not evaluated.

No whole-file or stable speed/memory-ratio claim follows from this single-pass development pilot. Sealed inputs and reserves were not accessed.

Recommendation: keep the current production backend. If a backend comparison is still desired, preregister a smaller feasibility study with its own explicit scope and budget. This outcome concerns feasibility under these limits; it does not reject context mixing or establish a compression-ratio winner.

## Reproducibility

- Machine-readable results, exact identities and commands (local record `backend-experiment-20261103.json`).
- Original baseline commit: `03e4de4aed039064479e8b31ace96bdf6a5f4644`; format v5, ABI 206; block size 4096; explicit `FAST_CHUNKS`; preset `9 | EXTREME`, CRC64, 64 MiB LZMA2 dictionary.
- PAQ8PX [official v217 release](https://github.com/hxim/paq8px/releases/tag/v217), source commit `c84f576fc2c522194cd320743708652a154daf6b`. Package SHA-256 `f5be98bff57db88e0bf1dc4ab309a87ab7c48fa169cc21d2fb89dd8180162f1c`; executable SHA-256 `4e530a62a69f7f5928f494d85492898ad21b43e3a2e9c2953a3307d5c5bb5ed4`. Sole official Windows x64 binary, runtime SIMD automatic, no extra tuning or pretrained weights.
- Python 3.11.15 and the exact `_lzma.pyd` binary are pinned in JSON; the statically linked liblzma version string is not exposed.
- Experimental decoder: magic `AHPSBX1\0`, version 1, codec/profile IDs, three 64-bit lengths, and SHA-256 hashes of original/container/native payload. Exactly 136 header bytes. It validates the stored artifact, dispatches the declared native backend, and inverses the stored v5 program directly.
- Local evidence: `experiments/backend-20261103/` contains the reviewed specification, frozen harness/resource runner, source package and notices, enforcement checks, raw logs, containers and archives. These generated artifacts are ignored by Git; this report records their identities.
