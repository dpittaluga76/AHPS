# Selective word-hybrid artifact audit

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Independent read/hash audit passed with no discrepancies
found in the checked artifacts, identities, plans, selection, arithmetic, or
execution evidence. The audit ran at Windows BELOW_NORMAL (`0x4000`). No codec,
test, or benchmark was rerun; only this audit note was created.

## Checks completed

- **20 saved archives and 20 restored files** match their recorded lengths and
  SHA-256 hashes. Each restored file matches its complete executable source.
  All five source files and full-file snapshots match the development manifest;
  the saved table regions match their recorded hashes too.
- Checked **241 identity references covering 133 distinct referenced files**,
  and read **62 run JSON files**. All **35 live pins and 35 frozen copies**
  agree. The native DLL and pinned Python/liblzma artifacts match their saved
  identities. Current production AHPS Python, C, DLL, and the existing AHRL
  core retain their previously verified hashes.
- All four historical completed AHPS archives reproduce the earlier corpus
  report's sizes and SHA-256 hashes exactly. Node has no historical completed
  baseline in that report and is correctly recorded as a fresh result.
- Every saved plan covers the entire source contiguously. Both aligned controls
  have identical boundaries and outside descriptors. Their table segments
  cover the complete declared table and contain exactly D1(12) → SPLIT(12)
  or WORD32(12) → SPLIT(12), with canonical `0c000000` parameters. Outside
  fragments match the baseline descriptors clipped to the table boundaries,
  including executable-origin rebasing relative to the original segment.
  Candidate plans agree with the saved validated container metadata, table
  lengths, segment counts, container sizes, and source CRC32 values.
- Selection is the stable minimum of the three complete archive sizes in
  original/byte/word order. All five selected archive files are byte-identical
  to their recorded winner and smaller than original AHPS. No measured case
  used the original fallback or a tie; those paths are covered by the saved
  preflight tests, not newly demonstrated by this corpus run.
- The record flags correctly distinguish **15 actual archive encodes** from
  **20 verified outputs**. The five selected archives are copies, not a fourth
  encode per file. Baselines record the production AHPS decoder; candidates
  and selected outputs record the research decoder.
- Every selected encoding component sum equals baseline encoding, baseline
  plan recovery, both aligned plan builds/encodes, and selection. The sums are
  not selection-only timings or standalone convenience-API timings. Verification,
  metadata serialization, and artifact I/O remain excluded as documented.
- All **five workers ran serially**, completed without errors or intervention,
  and report native ABI 206 and the frozen runtime. All **20 observed processes**
  terminated successfully. Job evidence confirms assignment before resume,
  no breakaway, verified limit flags, and process-tree cleanup. The limits stayed
  at **300 seconds per file, 900 seconds per batch, and 2 GiB aggregate committed
  memory per Job**. The batch took **267.219 seconds**; the maximum Job committed
  peak was **459,657,216 bytes**. These are resource-limit observations, not
  per-method memory or stable throughput measurements.
- Preparation, controller, worker-start, before/after method observations,
  worker-finish, and summary record BELOW_NORMAL priority. Saved before/after
  identity checks report success. The preflight log contains **136 passing
  tests in 5.139 seconds**, with no skips; it was read, not rerun.

## Recomputed complete-archive comparison

| File | Original AHPS | Aligned byte | Aligned word | Selected | Winner | Bytes saved vs original |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| 7zip-cli.exe | 181,776 | 181,616 | 181,492 | 181,492 | Word | 284 |
| chrome.exe | 1,628,332 | 1,621,304 | 1,621,476 | 1,621,304 | Byte | 7,028 |
| git-cli.exe | 1,473,840 | 1,462,352 | 1,460,872 | 1,460,872 | Word | 12,968 |
| node.exe | 21,768,312 | 21,769,004 | 21,766,840 | 21,766,840 | Word | 1,472 |
| vlc.exe | 301,752 | 301,436 | 300,676 | 300,676 | Word | 1,076 |
| Total | 25,354,012 | 25,335,712 | 25,331,356 | 25,331,184 | Four word, one byte | 22,828 |

All per-file and aggregate differences and percentages agree with independent
calculations. The selected total saves **0.0900370324%** of the original archive
total across **101,626,280 source bytes**. Always choosing word rather than
aligned byte saves 4,356 bytes in aggregate, with a 172-byte regression on
Chrome. Choosing the smaller of original/byte already saves 18,992 bytes;
allowing word saves another **3,836 bytes**, or **0.015130%** of original archive
bytes. This separates word arithmetic from the larger boundary/framing effect.
The human report's matrices, rounded percentages, incremental attribution,
counts, and six listed artifact hashes agree with the JSON evidence.

## Evidence identities

| Artifact | SHA-256 |
| --- | --- |
| JSON report | `6b18274009bcbf4f3854353e79b4aa71e730493ae700c1a13ef8eedb91dff684` |
| Freeze manifest | `de53006cac1217cc3ea2c5a6d5e8effea94bcce0b1019247ae222cd105e81570` |
| Preflight log | `f6c47894ec2f76da4106f0c7c6bbcf1ea8918366146f5855513cb4ca6603065d` |

The report is `reports/relational-hybrid-20261103.json`; local evidence remains
under `experiments/relational-hybrid-20261103/`. The preflight log is under
`experiments/relational-hybrid-preflight-20261103/`.

This audit verified saved bytes, hashes, metadata, and execution records; it did
not independently decode archives or replay transforms. The measured inputs are
five reused development files, not holdouts. PC activity and shared Job state
prevent stable speed or per-method memory claims. No production adoption,
general compression claim, source edit, frozen-artifact edit, or commit follows
from this audit.
