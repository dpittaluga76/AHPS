# Independent complete-file AHRL corpus audit

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

November 3, 2026. Reviewer: `relational_file_tests`. The evidence audit passes;
the experiment is **incomplete at 18 successful round trips out of 20 planned
method/file cells**. This review read existing files and records without running
an encoder, decoder, test suite or benchmark. Audit hashing ran below-normal.

## Complete inputs and correctness

The frozen report (local record `relational-corpus-20261103.json`) contains exactly the requested
five files: 7zip-cli, Chrome, Git, Node and VLC. All five original files and their
snapshots were rehashed against their manifest identities. No file was replaced
by a slice. Node is the full **91,426,304 bytes**.

All 18 actual archive files and restored outputs were rehashed independently.
Every restored length and SHA-256 matches its complete source. Node's solid XZ
and AHRL RAW results both restore the entire file. Its RAW wrapper has 11 frames;
physical wrapper parsing confirms their canonical lengths and complete coverage.
All 19 produced AHRL-wrapper frames across the available results were checked
against stored framing metadata, source CRC32, padding lengths and exact archive
boundaries. No decoder was rerun for this audit.

All live source/protocol/test pins, frozen source snapshots, runtime binaries,
input snapshots, batch records and Job records match their identities. The
production codec and existing AHRL core remain unchanged. The full test log
records **261 tests passing in 69.369 seconds, no skips**, with priority class
`0x4000` verified before execution. These include 18 new independent wrapper tests.

## Size comparisons and model attribution

| Complete comparison | AHPS bytes | AHRL AUTO bytes | AHRL RAW bytes | AUTO versus AHPS |
| --- | ---: | ---: | ---: | ---: |
| 7zip-cli.exe | 181,776 | 209,864 | 209,864 | 15.45% larger |
| chrome.exe | 1,628,332 | 1,777,948 | 1,777,948 | 9.19% larger |
| git-cli.exe | 1,473,840 | 1,666,048 | 1,666,048 | 13.04% larger |
| vlc.exe | 301,752 | 322,456 | 322,456 | 6.86% larger |

For all four completed comparisons, AUTO and the matched RAW archive are
byte-identical, verified by direct file comparison. Every completed AUTO frame
records RAW storage. The representation search therefore provides no size gain
over its matched control on those complete files. Percentages against AHPS,
matched RAW and solid XZ were recomputed independently and agree with the report.

Node's available sizes are 22,903,204 bytes for solid XZ and 23,663,876 bytes for
AHRL RAW. There is no completed AUTO archive or AHPS result for Node, so those
comparisons and a five-file aggregate are unavailable. The report correctly
leaves `totals` null and does not calculate a successful-subset corpus result.

## Timeout, priority and resource accounting

The exact 20-slot schedule is retained. Four file workers completed all four
methods. Node reached the **shared 300-second file-worker limit** during
`ahrl_auto:encode`; its AHPS cell had not started. The Job elapsed time was
300.047 seconds including termination. The first AUTO encode sample was at
127.609 seconds, leaving approximately 172.4 seconds of the shared budget for
that phase. This is not evidence that AUTO received a standalone 300-second
encoding budget.

All five worker trees ran serially, were assigned to their Jobs before resuming,
had breakaway disabled, and verified flags 9728 with a 2 GiB aggregate committed
memory cap. All trees, including timed-out Node, were terminated and cleaned up.
The batch completed in 380.0 seconds within its 900-second limit. There were no
benchmark retries or discarded completed observations.

Every worker records verified below-normal priority (`0x4000`) before heavy work;
every completed method also records it before encode, before decode and after
verification. The user was using the PC. Single-pass API timings and Job-wide
memory records do not establish stable throughput or per-method peak-memory
comparisons. The cause or duration of user-induced contention is not isolated.

This is a blind three-uint32 layout over complete executables, not automatic
structure discovery or the earlier known-table experiment. Its negative size
result must retain that scope. It does not reverse or overwrite prior results.

## Audited identities

| Artifact | SHA-256 |
| --- | --- |
| Corpus report | `e9db03b4937a9237f521f6f099b23ca4d63cc8399deb8bfc5d5d8111e16e5224` |
| Full test log | `94c561403cb5a4b07050428ee7d981c85d80e32d75df134b20c46c3343537c20` |

The full test log is retained at
`experiments/relational-corpus-preflight-20261103/tests.log`. The local independent
audit record is `experiments/relational-corpus-20261103/post-run-audit.json`.
