# Same-boundary table artifact audit

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Independent read/hash audit passed with no artifact, identity,
count, boundary, arithmetic, or Job-limit discrepancies found. The audit ran at
Windows BELOW_NORMAL priority (`0x4000`). It did not encode, decode, run tests,
or repeat the benchmark; only this audit note was created.

## Evidence verified

- **42/42 complete archive round trips and 30/30 residual diagnostic checks**
  are present. All 72 saved restored files match the corresponding source
  length and SHA-256. The 42 archives, 30 residual files, and 30 compressed
  residual probes match their recorded lengths and hashes.
- Checked **669 identity references**, covering **260 distinct files**, and
  parsed **170 saved run JSON files**. This includes all 31 live source, test,
  and protocol pins, their 31 frozen copies, the native DLL, and the three
  pinned Python/liblzma runtime artifacts. All frozen and live copies agree.
- All six complete parent files match the development manifest. An independent
  read of each PE header, exception-directory entry, and section mapping agrees
  with the reported table size and offset. Saved table bytes match those parent
  regions exactly. Every table has complete 12-byte records, sorted valid
  function ranges, and a size within the frozen 8 MiB cap.
- Every fixed AHPS program has one full-table segment starting at zero, with
  canonical `0c000000` parameters; RAW has an empty program. AHRL uses one
  width-4, three-field region with three previous-value models. Restored CRCs
  in the saved AHPS metadata agree with the source bytes.
- **Automatic AHPS and fixed DELTA1(12) → SPLIT(12) archives are byte-identical
  on all six inputs.** The saved automatic program is one full-table segment.
  Bare XZ and the RAW residual probe are also byte-identical on all six inputs.
- All six workers record native ABI 206, the same pinned DLL, and identical
  runtime identities. The current hashes of those runtime artifacts agree.
- All six jobs ran serially and completed successfully. All **24 observed
  processes** terminated with exit code zero. Saved Job evidence confirms
  assignment before resume, no breakaway, verified memory/kill-on-close flags,
  and process-tree cleanup. There were no sample failures, worker errors, or
  termination interventions. Limits remained **120 seconds per table,
  600 seconds per batch, and 2 GiB aggregate committed memory per Job**.
- Preparation, controller, worker-start, before/after codec observations,
  worker-finish, and summary priorities are recorded as BELOW_NORMAL. The
  batch completed in **29.297 seconds**. The largest Job committed-memory peak
  was **137,244,672 bytes**; this is not a per-method memory measurement.
- The saved preflight log contains **106 passing tests in 8.307 seconds**, with
  no skips, including the 17 same-boundary helper tests. These tests were not
  rerun for the audit.

## Independently recomputed comparison

Positive savings mean word delta is smaller. Archive columns count complete
formats. Residual columns are diagnostic XZ streams that omit transform
metadata; they are not standalone codecs. Their differences from archive sizes
must not be interpreted as additive metadata costs.

| Table | AHPS D1 / auto archive | Word archive | Archive saving | D1 residual XZ | Word residual XZ | Residual saving |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Blender, reused reference | 535,212 | 511,972 | +4.3422% | 535,600 | 512,004 | +4.4055% |
| 7zip-cli.exe | 5,892 | 5,780 | +1.9009% | 5,872 | 5,760 | +1.9074% |
| chrome.exe | 27,796 | 28,016 | -0.7915% | 27,716 | 27,992 | -0.9958% |
| git-cli.exe | 18,308 | 16,908 | +7.6469% | 18,276 | 16,908 | +7.4852% |
| node.exe | 204,648 | 203,464 | +0.5786% | 204,428 | 203,804 | +0.3052% |
| vlc.exe | 11,072 | 10,224 | +7.6590% | 11,032 | 10,200 | +7.5417% |

The report's per-input byte counts, selected best controls, percentages, and
candidate-support decisions agree with independent calculations. The human
report's full archive matrix, residual matrix, rounded percentages, counts,
and artifact hashes agree with the JSON evidence.

Word delta wins both comparisons on four of five development follow-ups, plus
the reused Blender reference. Chrome regresses. This supports testing an
optional word-delta candidate for this table class; it does not justify
replacing byte delta unconditionally. D2 has two-record warmup and is an
existing rival, not an order-matched arithmetic control.

## Evidence hashes

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `350e475de9f7d9da68e36a949f04c9cbb0cd498de395e3f6055fa9494021239c` |
| Freeze manifest | `5ddef018b16235b7c12f4e31a8067f51a8b18495ea99da154c01270d28464581` |
| Preflight log | `7e8940b0ac02f14c1eb0204cb4cf78700b2bc57a8b76dda9835e2322dddfc9a4` |
| Comparison harness | `8286eb55981c28f77b3740f0685fa44e2f4bb3d3b520564ed67c5df6c864c159` |
| Helper tests | `f1e459ef69244ad01bc2f9f2564cc164affa35df362f691ea0f03fd5940172a4` |

The audited research HEAD is `de5f78c89579565de07f8c343fc0132bfb647336` on
`codex/relational-compression`; the freeze declares main baseline `0dac8d2`.
The report is `reports/same-boundary-tables-20261103.json`, with local evidence
under `experiments/same-boundary-tables-20261103/` and the preflight log under
`experiments/same-boundary-preflight-20261103/`.

These are complete exception-table results, not whole-executable savings or
final holdout evidence. Single observations while the maintainer used the PC do not
establish throughput differences. This audit verifies saved bytes, identities,
metadata, arithmetic, and execution evidence; it does not independently repeat
the codec transformations. No source, frozen artifact, existing report, or
commit was changed by the audit.
