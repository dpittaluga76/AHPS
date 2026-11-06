# CSV compression experiment

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Measured on 2026-11-05 using the user-supplied `gutenberg-dpo.csv`.
The input is 18,013,761 bytes with SHA-256
`9219ad3684c4d7325ce5b54db7056a41b61e6512e4bef2b844c64ef386e5e603`.
It contains a header and 918 data rows with six columns, a UTF-8 BOM, CRLF
record endings, and quoted multiline text.

The new optional `--csv` mode groups original field spans by column before
LZMA2 compression. A length table permits exact reconstruction. Every original
quote, separator, and newline is preserved. The complete candidate archive is
compared with the usual AHPS result at the same resolved preset, and the smaller
archive is selected. The existing result wins ties. Unsupported inputs use the
usual AHPS result.

## Results

| Setting | Usual AHPS bytes | CSV mode bytes | Bytes saved | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Default, resolved preset 3 | 5,658,236 | 5,540,080 | 118,156 | 2.09% |
| Preset 9 + EXTREME | 4,608,796 | 4,546,412 | 62,384 | 1.35% |

Plain XZ produced 5,658,212 bytes at preset 3 and 4,608,320 bytes at preset 9
with EXTREME. CSV mode therefore also improves on the direct XZ controls.
The CSV container before XZ compression is 18,035,846 bytes, including 22,085
bytes of header and length-table overhead. All archive sizes include that overhead.

Before the speed optimization below, the initial baseline API run took 9.14
seconds to encode. The subsequent
default CSV CLI benchmark reported 8.91 seconds for AHPS encoding, including
both candidates. These single observations are affected by warm-up and system
variation and do not establish a speed improvement. At preset 9, the usual AHPS
run took 9.10 seconds and CSV mode took 17.22 seconds. CSV mode adds a full XZ
trial and should be expected to cost additional encoding time.

Runs were serial under the repository benchmark lock. The baseline and CLI
comparison workers each had a 300-second deadline and an 8 GiB committed-memory
cap. All completed. Each full-file benchmark verified exact recovery with
SHA-256. These results cover one development input and one observation per
setting, so broader CSV performance remains unmeasured.

The focused regression suite passed 144 tests, including CSV byte preservation,
malformed archives, decoder resource limits, atomic file output, CLI option
handling, existing archive fixtures, native/Python decoding, and search defaults.
The actual `compress --csv` and `decompress` CLI commands also restored the full
input with the same SHA-256 through the file API.

## Encoding speed optimization

Two changes reduce elapsed encoding time while retaining exact archive selection.
Chunk planning skips a compression trial when only one plan exists, because that
trial's compressed bytes were discarded. Supported CSV inputs of at least 1 MiB
on multicore machines run their final baseline and CSV compression trials
concurrently, using one additional worker thread. The smaller complete archive
still wins, with the baseline retained on ties. Small inputs and single-core
machines use sequential trials.

Profiling the initial CSV implementation found that column grouping took about
0.04 seconds. Two singleton chunk trials consumed about 2.24 seconds at preset 3
and 0.70 seconds at preset 9. Final baseline and CSV compression dominated the
remaining time. The new report measures the concurrent compression stage once
in wall-clock time.

The following measurements compare the initial CSV implementation with the
optimized CSV implementation on the same input. Values are medians of three
fresh-process runs per version and preset, with version order alternated between
pairs. Timings cover the AHPS encoding API, excluding the benchmark CLI's gzip
and plain-XZ controls, file reads, and subsequent round-trip checks.

| Setting | Before | After | Time reduction | Archive bytes |
| --- | ---: | ---: | ---: | ---: |
| Preset 3, the file's default | 7.306 s | 3.428 s | 53.08% | 5,540,080 |
| Preset 9 + EXTREME | 15.863 s | 9.261 s | 41.62% | 4,546,412 |

All six before/after pairs produced byte-identical archives and passed exact
round-trip verification. Archive SHA-256 values were
`d72607da829e0a9e4a252ae97c8f638b72c393e64319cc57e50a34b48f2c6a32`
at preset 3 and
`53dec5b9acbd52f6831aeac0116ebfadfd518d2e9dfddeaa79ad0303d8eeb141`
at preset 9.

Concurrent compression uses more memory. The maximum kernel peak process
working set across the three runs increased from 119.6 to 158.2 MiB at preset 3,
and from 299.2 to 528.5 MiB at preset 9. Each qualifying API call can run two
compressors simultaneously. Repeated concurrent callers multiply that resource
cost.

The comparison held the repository benchmark lock, ran one worker process at a
time, and retained the 300-second worker and 8 GiB committed-memory caps.
Both encoder source hashes and full execution captures are saved in
`experiments/csv-speed-20261105/`, with per-run metrics in `results.json`.
The measurements apply to this file and machine.

After optimization, 149 focused tests passed. Added coverage checks skipped
singleton trials, final archive construction, sequential/concurrent archive
identity, fallback and ties, worker eligibility, exception propagation, worker
cleanup, and wall-clock reporting.

## Reproduction

```powershell
python ahps.py bench "gutenberg-dpo.csv"
python ahps.py bench "gutenberg-dpo.csv" --csv
python ahps.py bench "gutenberg-dpo.csv" --preset 9
python ahps.py bench "gutenberg-dpo.csv" --csv --preset 9
```

Use `csv_mode=True` with `ahps_compress_bytes` to enable the same selection in
Python. Both public decompression APIs recognize selected CSV archives
automatically. They use the new `AHPS\x06` container and require the updated
reader and `ahps_csv.py`. CSV decoding buffers the full transformed container,
including through the file API. Existing archive formats remain readable.

The supported dialect and bounds are specified in [the format contract](../docs/format.md).
The local execution captures are under `experiments/csv-20261105/`.
