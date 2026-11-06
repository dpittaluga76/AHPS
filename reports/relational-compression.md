# First relational compression prototype

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Local branch `codex/relational-compression`, based on `main`
at `0dac8d2`. The prototype works and preserves exact bytes. The first real-data
screen **does not establish an additional cross-field prediction gain**.

On Blender's complete 2,484,204-byte x64 exception table, the record-aware
integer-delta control produced **511,972 bytes**, versus **535,212 bytes** for
the frozen production AHPS implementation: **4.34% smaller on this region**.
Adaptive prediction chose that same delta model for all fields and added no
gain. This is an opportunity in known record layouts, not evidence of a new
cross-field breakthrough or a whole-executable improvement.

## What was implemented

[`research/relational.py`](../research/relational.py) implements an independent
bounded lossless format. It supports raw bytes, byte shuffle, previous-record
word deltas, affine predictions from earlier fields, and a bounded adaptive
selector. An independent-field ablation allows raw/history/constant choices
without using other field values. `auto` measures six complete archives and
returns the smallest, with all model and framing bytes included.

Predictor arithmetic is exact modulo the word width. All bit patterns, including
floating-point encodings, are preserved. Decoding performs no model search.
The decoder validates XZ checks, dimensions, descriptors, causality, actual
expansion, declared lengths, trailing bytes and restored-data CRC. This is a
buffered Python research implementation with an 8 MiB source cap, not a streaming
production codec. Usage is documented in the [research README](../research/README.md).

The production Python/C/DLL, archive format, defaults and baseline tests were
not changed. The accepted POLY2 implementation remains in place.

## Real-table comparison

Each row encodes the same complete table with LZMA/XZ preset 6 and CRC64.
AHPS uses the pinned native ABI 206, block size 4096 and explicit `FAST_CHUNKS`.
This differs from the historical whole-Blender preset-9 comparison; its ratios
must not be combined with these results.

| Method | Complete archive bytes | Encode API seconds | Decode API seconds |
| --- | ---: | ---: | ---: |
| Bare XZ | 855,704 | 0.304379 | 0.034452 |
| Frozen AHPS | 535,212 | 0.721710 | 0.022339 |
| Research RAW | 859,612 | 0.312999 | 0.035250 |
| Byte shuffle | 579,172 | 0.272163 | 0.022745 |
| Word delta + byte shuffle | **511,972** | 0.436461 | 0.137202 |
| Independent-field selection | **511,972** | 0.436467 | 0.138103 |
| Fixed cross-field prediction | 602,260 | 0.387397 | 0.148314 |
| Adaptive prediction | **511,972** | 0.457037 | 0.139348 |
| Auto (selected delta) | **511,972** | 2.281183 | 0.139080 |

Times are one-pass API diagnostics, excluding file I/O and verification. Methods
share one input worker and run in a fixed order; these are not stable throughput
estimates. The prototype's observed delta decode was substantially slower than
native AHPS. No per-method peak-memory comparison was made. Auto's time includes
all six complete candidate encodes.

The three selected adaptive descriptors were all previous-record predictions;
none used another current field. Its archive equals the delta archive. The
predeclared continuation gate required at least 1% less size than the best
non-cross control and an actually used nonzero affine dependency. It failed
both conditions: saving against that control was 0%, with no cross-field model.
The record layout and word arithmetic are a combined change relative to AHPS;
this test does not separate their individual contributions.

The bounded selector uses a short-prefix byte-histogram estimate. This result
does not establish that every cross-field model, mixed model combination or
selection procedure would fail. None was retuned after observing this table.

## Synthetic controls

Each constructed input contains 16,384 records of three uint32 fields (196,608
bytes). They demonstrate behavior under deliberately planted structure and its
absence; they are not evidence of a real-world size gain.

| Input | Best non-cross control | Best cross-field archive | Difference |
| --- | ---: | ---: | ---: |
| Exact planted affine relationships | 196,680 | 65,832 | 66.53% smaller |
| Planted relationships with sparse errors | 196,680 | 67,048 | 65.91% smaller |
| Independent random fields | 196,680 | 196,756 | 76 bytes larger |

On independent fields, auto chose framed RAW at 196,704 bytes: 24 bytes larger
than bare XZ. Its fallback guarantee applies to its own framed candidates, not
to every other compressor or every possible input.

## Cohorts, limits and correctness

The [first frozen protocol](../docs/relational-compression.md) capped the real
table at 2 MiB. Header-only inspection found 2,484,204 bytes, so its nine real
cells were correctly recorded unavailable. Its 27 synthetic cells all restored
exactly. The original report remains unchanged and its criterion remains unmet.

Before any real-table compression, a [separate follow-up](../docs/relational-table-followup.md)
raised only the table eligibility cap to 3 MiB. It used the same codec, tests,
models, controls, settings, source parent and criterion. Its nine real-table
round trips passed. Sources and protocols were frozen separately; the original
harness snapshot is preserved alongside the revised profile-aware harness.

The full regression suite passed **243 tests, no skips**, in 20.063 seconds.
Independent tests cover modular wraparound, even coefficients, known wire
vectors, all seven methods, fit/score separation, arbitrary float bit patterns,
malformed models and bounded expansion. **All 36 actually executed comparison
cells restored the exact source length and SHA-256**, across the two cohorts.
There were no compression retries or discarded observations.

The batches ran serially under the shared benchmark lock. Each input worker
had a 120-second deadline and 2 GiB aggregate committed-memory cap, within a
600-second batch limit. The first batch took 3.141 seconds; the table-only batch
took 7.719 seconds. All measured workers exited successfully and were cleaned up.

The real region is `.pdata`, file offset 109,946,368, RVA 122,339,328, with
207,017 records: begin RVA, end RVA and unwind RVA. Parent SHA-256 was checked
before and after extraction. No sealed input, reserve, external codec or online
source was used.

## Evidence and next decision

- Original cohort and all planned outcomes (local record `relational-first-20261103.json`).
- Real-table follow-up and all nine measurements (local record `relational-table-20261103.json`).
- [Independent evidence audit](relational-audit-20261103.md).
- [Regression tests](../tests/test_relational.py).
- Local artifacts: `experiments/relational-20261103/` and
  `experiments/relational-table-20261103/` retain frozen source snapshots, input
  bytes, archives, restored files, resource records and logs.
- Full test log: `experiments/relational-preflight-20261103/tests.log`, SHA-256
  `908c8a07320621ea22b4654aa87ee36ea5f1b52587970001532cce02c0b220fb`.

Keep this implementation as a research tool. The cross-field proposal has not
earned promotion under the declared criterion. The measured record-aware delta
gain is a separate follow-up question: its usefulness on an independent known
layout and its reconstruction cost need evaluation before production adoption.
