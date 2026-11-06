# Terminal progress validation

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

The CLI now displays a percentage bar, stage, elapsed time, and activity spinner for compression, decompression, single-file benchmarking, and self-test. The corpus benchmark runner displays completed jobs across files, codecs, and repetitions. Display is automatic when stderr is a terminal. `--progress` forces it and `--no-progress` disables it.

Percentages measure completed workflow stages. Existing chunk and segment boundaries provide finer updates where available. Native planning and one-shot LZMA calls remain unchanged and hold their percentage while the spinner moves. There is no estimated-time interpolation. A successful final frame follows saving output or passing round-trip verification. Handled corpus failures produce a failed final status.

The standard-library renderer redraws at most four times per second, apart from initial and final frames. A separate thread handles periodic terminal writes. The codec thread records only small state updates at existing boundaries. Disabled display starts no renderer thread and passes no progress callbacks. The core APIs remain silent by default. Compression options, native interfaces, buffer sizes, and compression call boundaries are unchanged.

## Timing check

The test used the saved 15 MiB `chunk_02.bin` with ordinary AHPS preset 3. Its SHA-256 is `322439bdd24a2a3963eb79ef0bd22ef75f285c122c9f0913d37674bd39056011`. The 11,806,492-byte archive was identical with progress enabled and disabled.

After one warmup encode, the worker alternated on/off order across four compression pairs and eight file-decompression pairs. Every decode was compared byte for byte with the input. Measurements include entering and closing the progress context. Input loading and equality checks are outside the measured interval. The enabled renderer wrote and flushed its frames to a local text file, so this measures rendering/thread costs and file output rather than a particular terminal application's repaint costs.

| Operation | Disabled median | Enabled median | Median change |
| --- | ---: | ---: | ---: |
| Compression | 5.3022 s | 5.2415 s | -1.14% |
| File decompression | 0.3541 s | 0.3563 s | +0.63% / +2.23 ms |

The timing ranges overlap. Compression ranged from 5.1405 to 5.4317 seconds disabled and 4.9555 to 5.4908 seconds enabled. Decompression ranged from 0.3377 to 0.4228 seconds disabled and 0.3347 to 0.3988 seconds enabled. These observations show a small timing difference within the observed run variation. They do not prove literally zero overhead or establish performance for every terminal and workload.

The measurements ran sequentially under the benchmark lock in a Windows Job Object with a 240-second deadline and an 8 GiB committed-memory cap. The raw results, worker record, render trace, and reproduction script are under `experiments/progress-20261106/`.

## Verification

New tests cover automatic TTY detection, disabling all renderer activity, redraw throttling, monotonic percentages, thread shutdown, terminal write failures, and success/failure completion. Integration checks cover identical archive bytes, normal/CSV/tensor representations, buffered and file decoders, chunk planning, corruption and atomic-output failures, both flag positions, unchanged stdout reports, and benchmark verification failure. Corpus-runner tests preserve JSONL records and per-job output, protect/fingerprint the new dependency, and keep isolated workers free of progress display.

The final broad regression run executed 259 tests. **252 passed**, and seven existing hard-link protection tests failed while creating their fixtures because this Windows sandbox denies hard links. A separate probe confirmed that workspace hard-link creation is also denied. Those seven tests remain blocked by the environment, and their assertions were not weakened. All 25 new renderer, CLI integration, and corpus-progress tests passed.
