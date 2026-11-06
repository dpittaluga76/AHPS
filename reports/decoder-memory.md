# Incremental decoder assembly: implemented, tested, not retained

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Task 8 completed on November 3, 2026. The candidate passed correctness checks
but missed the predefined memory and time targets on Blender. Production
`ahps.py` and its file-decoder tests were restored to the exact task-8 baseline.
The candidate, tests, patch, frozen inputs and all measured results are preserved.

| Input | Reference peak MiB | Candidate peak MiB | Memory saving MiB | Reference decode s | Candidate decode s | Median time change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Blender | 140.559 | 137.703 | 2.855 | 1.181120 | 1.274501 | +7.91% |
| MR | 47.539 | 47.371 | 0.168 | 0.114299 | 0.115797 | +1.31% |
| Synthetic RAW | 52.063 | 51.199 | 0.863 | 0.040590 | 0.040313 | −0.68% |

These are medians of five fresh-process observations per implementation/input,
with alternating paired order. Peak working set was captured before restored
output verification. The [full report](decoder-memory-20261103.md) preserves
every observation, ranges, process costs and exact thresholds.

The required Blender memory reduction was **at least 8 MiB**, and the allowed
median decode-time increase was **at most 5%** on each input. Blender missed
both. All five Blender candidate runs were slower than their paired reference
runs. MR and RAW passed their time and memory guards. No benchmark was retried,
excluded or substituted to change this verdict.

The candidate changed only `ContainerStream.read`: it grew a mutable destination
from received bytes and released consumed pieces before the next pull. Native
code, archive layout, scratch policy, search and the encoder were unchanged.
All **224 regression tests passed without skips**, and all **30 measured decodes**
restored the pinned length and SHA-256. The implementation review and streaming
allocation tests confirmed that forged large declarations cannot demand their
full claimed allocation before data arrives.

The expected reduction in logical source-piece overlap did not translate into
the targeted process-memory saving. Buffer growth/reallocation and allocator
behavior are possible contributors; this comparison does not isolate a cause.
The result rejects this candidate under its saved criteria. It does not establish
that every approach to decoder memory reduction will fail.

Targeted tests also reproduced a pre-existing false-truncation edge case when
artificially shrinking decoder/input chunk sizes: reference and candidate had
identical outcomes, while the normal configuration passed. The unchanged `pull`
code is outside this experiment. The reproducer and results are retained in
`experiments/decoder-memory-20261103/preexisting-pull-issue.txt` and `.json`.

The production Python SHA-256 after restoration is
`c7dcc554c64f4abf78fef6852603e91de9db4048ae5ffe81c4ff007b61eead2d`.
The native C/DLL hashes still match the baseline. The isolated candidate Python
SHA-256 is `52aa28b3bf5b24b014820c6c2df85dc8dfd18a4908d71a9211b07cbe9dc5edfe`.
The earlier accepted POLY2 work remains intact.

Evidence: frozen comparison and all records (local record `decoder-memory-20261103.json`),
[original contract](../docs/decoder-memory-experiment.md), and local artifacts
under `experiments/decoder-memory-20261103/`. Candidate tests are preserved under
`candidate/tests/`; correctness log, scope audit and candidate patch are kept
alongside them. No sealed data or external-codec measurement was used.
