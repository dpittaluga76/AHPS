# Initial isolated Blender baseline

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

AHPS produced a **3.34% smaller archive** than BCJ2, with **6.52x encoding
time** and **4.91x decoding peak working set**. Blender influenced this design;
this is development evidence, not an independent acceptance result.

| Metric | AHPS | 7-Zip BCJ2 |
| --- | ---: | ---: |
| Complete archive bytes | 26,380,608 | 27,292,035 |
| Median encoding time | 187.410 s | 28.735 s |
| Median decoding time | 1.544 s | 0.626 s |
| Median encoding peak working set | 1,006.92 MiB | 706.80 MiB |
| Median decoding peak working set | 382.66 MiB | 77.90 MiB |

Three fresh encode processes and three separate decode processes were run per
codec, serially. All six round trips passed SHA-256 verification. Each codec
produced exactly the same archive size and SHA-256 in all three repetitions.
No retries, failures or omitted repetitions occurred.

| Codec / repetition | Encode seconds | Decode seconds | Encode peak MiB | Decode peak MiB |
| --- | ---: | ---: | ---: | ---: |
| AHPS 1 | 191.255 | 1.544 | 1,006.895 | 382.660 |
| AHPS 2 | 187.410 | 1.493 | 1,006.918 | 382.766 |
| AHPS 3 | 187.003 | 1.677 | 1,006.941 | 382.664 |
| BCJ2 1 | 28.735 | 0.626 | 706.801 | 77.891 |
| BCJ2 2 | 28.802 | 0.624 | 706.816 | 77.902 |
| BCJ2 3 | 28.144 | 0.630 | 706.770 | 77.926 |

## Reproduction and evidence

- Candidate/harness commit: `1d436f33090276b64a841709393833f4e15bef27`.
- Source: `blender.exe`, 113,041,880 bytes, SHA-256
  `9d5df7a9ac2a7c5085aa0669700021bfa5c5e92de167a2510b0d0942c34deab0`.
- AHPS preset 9 extreme, block size 4096, native ABI 206, default exhaustive
  selection. 7-Zip 26.00 x64, `-t7z -mx=9 -md=64m -mf=BCJ2 -mmt=1 -ms=off`.
- Actual 7z file-entry method:
  `BCJ2 LZMA2:26 LZMA:20:lc0:lp2 LZMA:20:lc0:lp2`.
- Windows x64, AMD Ryzen 5 7600 (6 cores / 12 logical processors), CPython
  3.12.14. Windows reported 67,802,910,720 bytes physical memory; Balanced power
  scheme was queried after the run, not sampled within each worker.
- 900-second timeout per phase; no OS process-memory hard cap. No cache flush;
  decoding followed encoding. Primary timing includes process startup, imports,
  input/output and shutdown. Hash checks occur outside measured worker operations.
- The shared runner was announced in root `xchange.txt`. The core reviewer held tests/builds;
  other agents made text edits only. Ordinary background OS/application activity
  was not disabled or comprehensively monitored.

Command from the root checkout, using the pinned bundled Python interpreter:

```text
python -B benchmark_ahps.py --files blender --codecs ahps 7z-bcj2 --repeats 3 --preset 9 --timeout 900 --output reports/baseline-blender-step2.jsonl
```

The adjacent JSONL contains exact commands, executable/DLL/interpreter/liblzma
and manifest hashes, actual file listings, program summaries, and every phase's
measurements. Its SHA-256 is
`e1f9b5c2f88685666b4cbf1e14bdb3e735a6c61053401c249c213794bd279e8e`.

## Implications

On this development file, the size saving exceeds the proposed 2% per-file mark,
but both proposed product ratios are exceeded. Reaching the 3x time ratio would
require about 54% less encoding time at this baseline speed. Reaching the 2x
memory ratio would require about 59% less decoding peak working set.

Search-cost changes target encoding work. They do not by themselves demonstrate
a reduction in decoding memory. The decoder's buffer lifetimes need separate
inspection before choosing a memory optimization; no allocation profile was
collected in this run.

Subsequent component comparisons must use one fixed implementation with the
declared feature settings. These measurements are not mixed with historical
concurrent timings. No sealed corpus file was opened or measured.
