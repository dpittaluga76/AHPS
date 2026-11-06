# POLY2 deduplication validation

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Tasks 1 and 2 of the [task queue](../docs/next-steps.md) are complete.

Removing the second polynomial candidate preserved every compared score, selected parameter, plan, container, and archive byte within each engine. All 220 regression tests passed, including five new tests for canonical selection, coefficient wraparound, and decoding archives with equivalent noncanonical coefficients. Archive v5 and native ABI 206 are unchanged.

## Frozen reference

The reference is commit `03e4de4aed039064479e8b31ace96bdf6a5f4644`, with the pre-existing planning-document edits recorded separately. Before editing either scorer, the original Python/C source, shipped DLL, format contract, and corpus manifest were copied into `experiments/poly2-dedup-20261103/reference`. Reference outputs were captured before the code change.

The original DLL was retained separately from a rebuild of the unchanged source. Reference and candidate rebuilds used GCC 16.2.0 with `-O3 -mavx2 -mpopcnt -shared`. The rebuilt reference matched all 254 scorer cases per engine and all 28 smaller archive cases. The full Blender reference used the original DLL; it was not repeated with the rebuilt reference.

The freeze records exact source/DLL/compiler hashes, Python 3.11.15 and platform identity, input hashes, build commands, and settings. Python's statically linked `_lzma.pyd` does not expose a liblzma version string, so the extension's exact binary hash is recorded instead. All comparisons used the same Python/backend runtime.

Each archive used block size 4096, explicit preset 9 resolved to 9 + EXTREME, explicit `FAST_CHUNKS`, and all existing plan features enabled. No search defaults or decoder semantics changed.

## Comparison coverage

| Check | Result |
| --- | --- |
| Native scorer, reference versus candidate | 254 cases; all cost bits and local parameters identical |
| Python scorer, reference versus candidate | 254 cases; all cost bits and local parameters identical |
| Complete archives | 29 of 29 byte-identical |
| Program tables and decoded containers | 29 of 29 byte-identical |
| Restored original bytes | All comparisons passed |
| Regression suite | 220 tests passed in 25.815 seconds; no skips |

The scorer cases cover all 128 canonical quadratic coefficients, short-input gates, byte/block boundaries, constants, random data, all four executable-scoring flag combinations across boundary cases, and development/fixture samples. Exact floating-point comparisons are made within each engine. The original native/Python scorers already differed in 66 cost rows and 23 local parameter selections; the candidate preserves both engines' existing results exactly.

The archive matrix contains 15 inputs: full Blender with native encoding; fixed 64 KiB slices of Blender, Node, MR, X-ray, and Webster with both encoders; four deterministic synthetic inputs with both encoders; and restored inputs from five preserved v3 fixtures with both encoders. All small archives were decoded with both engines. Full Blender was decoded with the native engine. Full development-parent hashes were checked against the manifest before extracting slices. No sealed inputs were read.

The full Blender input is 113,041,880 bytes. Both archives are **26,380,608 bytes**, with SHA-256:

```text
975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd
```

## Timing observations

These checks establish output identity, not a general speed improvement.

| Diagnostic | Reference | Candidate |
| --- | ---: | ---: |
| Full Blender encode, one run per build | 183.404 s | 205.549 s |
| Native `score_all`, median of seven alternating 100-call batches | 54.398 ms | 53.434 ms |
| Native `score_all`, batch range | 52.397–55.340 ms | 50.203–56.452 ms |

The scorer timing uses 16,386 synthetic bytes per call and includes the Python/native bridge and result allocation. Its ranges overlap. The candidate's single Blender encode was slower; these unpaired single runs do not identify a cause or establish a stable performance regression or improvement. Detailed cost attribution remains task 3. The scorer-check elapsed times in the raw capture records also include case generation and file I/O and must not be treated as kernel benchmarks.

## Evidence and reproduction

- Machine-readable evidence (local record `poly2-dedup-validation-20261103.json`): frozen provenance, declared inputs, complete comparison results, build hashes/commands, and diagnostic timing samples.
- Integration log (local record `poly2-integration-tests-20261103.log`): `python -B -m unittest discover -v` against the installed candidate DLL.
- [Regression tests](../tests/test_poly2_canonical.py): frozen scorer expectations, independent residual construction, and noncanonical archive compatibility.
- Local artifacts: `experiments/poly2-dedup-20261103/` retains the original and rebuilt reference, candidate, input snapshots, plans, containers, archives, `freeze.json`, `cases.json`, and the verification/finalization scripts. This directory is ignored by Git; the evidence JSON records its identities and location.

The verification script's `capture --label reference --full` ran before edits; `capture --label rebuilt-reference` checked the rebuild; `capture --label candidate --full` checked the candidate. Captures refuse to overwrite existing artifact directories. Finalization checked matrix completeness, compared actual bytes again, verified frozen-source hashes, and confirmed the installed Python/C/DLL files match the tested candidate.

The installed `ahps_simd.dll` is the verified candidate build. This validation did not run decoder profiling, standalone SPLIT, generator gating, or the saved backend pilot. Subsequent profiling is documented in the [profiling report](profiling.md). These reference artifacts do not replace the backend pilot's separately timed preparation.
