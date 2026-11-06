# Independent backend artifact audit

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. The bounded read/hash audit found no codec-output, identity,
header, selection, or arithmetic discrepancy. One process-sampling gap is
documented below. Audit work used verified Windows BELOW_NORMAL (`0x4000`);
no codec, test, build, or benchmark was rerun. Only this audit note was written.

## Verified evidence

- All **30 actual saved archives and 30 restored files** match their recorded
  lengths and SHA-256 hashes. Every restored file matches the complete parent
  executable. All five sources and full-file snapshots match the development
  manifest; table-region hashes and bounds also agree.
- Checked **327 identity references across 172 distinct referenced files** and
  read **82 run JSON files**. All **42 live pins and 42 frozen copies** agree.
  Runtime, compiler/tool, source, DLL, build-manifest, and dependency identities
  match their frozen records. Production AHPS Python/C/DLL hashes are unchanged.
- All **five AHPS baseline archives** reproduce the preceding hybrid report's
  exact lengths and SHA-256 hashes, including Node.
- Directly parsed the **25 AHRC headers**: 20 representation trials plus five
  selected copies. Every output has AHRC1 magic, zero reserved fields, coded
  mode, the correct representation, exact source/payload lengths, and valid
  table bounds. Header source CRCs match the complete sources; residual CRCs
  agree with saved metadata. Bit counts, exact packed lengths, final zero bits,
  and byte padding agree. No measured AHRC output used stored mode or XZ framing.
  Stored fallback remains a tested capability, not a measured outcome here.
- All **75 independence-guard scopes** entered and exited with zero attempted
  forbidden calls and the exact 26-entrypoint frozen guard list. The phases are
  encode/decode/inspection for each trial and selection/decode/inspection for
  each selected copy. Baseline AHPS is comparison-only and is outside these
  candidate scopes.
- Independently parsed the custom DLL's PE import descriptors. They match the
  build record: five Windows CRT API-set imports and KERNEL32, resolving in the
  recorded build evidence to UCRTBASE and KERNEL32. No imported compression
  library appears. The recorded GCC command, ABI 1, compiler identity, build
  priority, and dependency hashes agree. This complements the saved fresh-process
  import-denial test and independent arithmetic/token wire tests; they were
  not rerun for this audit.
- Each selected file is byte-identical to the stable minimum of **only raw,
  exe, table_byte, and table_word**, in that order. Every selected archive is
  larger than AHPS; the loss is retained rather than hidden by an AHPS fallback.
- Records distinguish **25 encode operations** from **30 verified outputs**:
  five AHPS references, twenty AHRC trials, and five selected copies. Encoding
  component sums include the four AHRC trial APIs plus selection and exclude
  AHPS. They match the saved API durations exactly and are not separately timed
  automatic-encoder calls or throughput measurements.
- The preflight log contains **135 passing tests in 9.342 seconds**, with no
  skips, including the 22 independent-backend tests and fresh-process codec
  import denial. The human report's matrices, rounded percentages, and all nine
  listed artifact hashes agree with the JSON evidence.

## Recomputed result

| Complete file | AHPS bytes | AHRC raw bytes | Selected AHRC bytes | Selected mode | Larger than AHPS |
| --- | ---: | ---: | ---: | --- | ---: |
| 7zip-cli.exe | 181,776 | 250,553 | 210,390 | Table byte | 15.741352% |
| chrome.exe | 1,628,332 | 2,093,652 | 1,880,606 | Table byte | 15.492786% |
| git-cli.exe | 1,473,840 | 1,920,058 | 1,671,411 | Table word | 13.405186% |
| node.exe | 21,768,312 | 28,941,038 | 27,418,123 | Table byte | 25.954291% |
| vlc.exe | 301,752 | 383,218 | 338,777 | Table word | 12.270010% |
| Total | 25,354,012 | 33,588,519 | 31,519,307 | Three byte, two word | 24.316842% |

All per-file and aggregate differences and percentages recompute correctly.
Selected AHRC adds **6,165,295 bytes** versus AHPS, or **24.3168418474%**. It saves
**2,069,212 bytes** versus the new backend's raw representation. Forcing word
instead of byte table arithmetic adds 2,817 bytes in aggregate; word wins only
on Git and VLC. The complete sources total **101,626,280 bytes**.

## Resource evidence and sampling limitation

All five workers completed successfully and ran serially. Preparation,
controller, worker-start, before/after API observations, worker-finish, and
summary priorities are recorded as BELOW_NORMAL. The unchanged limits were
**300 seconds per file, 900 seconds per batch, and 2 GiB aggregate committed
memory per Job**, with assignment before resume, no breakaway, verified limit
flags, and tree cleanup. The batch took **181.109 seconds**; Node took
**158.469 seconds**. The largest kernel Job committed-memory peak was
**718,254,080 bytes**. No worker timed out or required termination intervention.

VLC records one `OpenProcess` sampling failure, Windows error 87, for PID 24912.
Its Job event log records both creation and exit at the same 0.344-second
observation, followed later by the Job's active-process-zero event. Across the
batch, kernel Job counts total 20 processes, while 19 process-detail records
were captured; all 19 report successful termination. Thus the missing short-lived
process's individual resource details were not observed. VLC's worker exited
zero and tree cleanup was verified. This limits sampled per-process resource
evidence, not the independently verified archive sizes, restored hashes, or
kernel Job memory limit. No claim of zero sampling failures is made.

## Evidence hashes

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `aa7685b750b2e4924f7043418fc0c596ae7bb4a75ce60999bf63d007be28a3e1` |
| Freeze manifest | `702a956b1d84245764659df1d96e4bda9b850ba3184ea90a5f6f6063109000d7` |
| Preflight log | `324c58a5a108d5ddd174c2c22ee19c4531e2f70aa35686e05fa072437ac39f04` |
| Build manifest | `618b2a0c360469dbd207200e202bf70a5678bf2e4c10b26b7b39900bd6bfb45d` |
| Backend DLL | `2677ecc67598595d6d18dc5ced8a54596d084f32b7599fca257e8bb4e2165c30` |

Saved evidence remains under `experiments/independent-backend-20261103/`; the
report is `reports/independent-backend-20261103.json`. This audit independently
read bytes, hashes, headers, PE imports, and execution records. It did not decode
payloads or replay residual transforms, so transformed-residual CRC verification
is supported by the saved guarded decode/restoration evidence. These five reused
development files do not establish general compression performance, stable
speed, or per-codec memory usage. The backend is independent, but these measured
sizes favor AHPS. No retuning, production change, or commit was performed.
