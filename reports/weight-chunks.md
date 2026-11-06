# Z-Image weight-chunk compression

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

This report records the initial generic comparison. The subsequent [safetensors-aware AHPS enhancement](safetensors-aware.md) produces smaller archives on all three chunks and is integrated into the normal AHPS encoder and decoder.

Three consecutive 15 MiB chunks were extracted from the beginning of `z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors`. Each chunk is exactly 15,728,640 bytes. The source is 6,158,115,074 bytes. Sampling uses MiB to match AHPS chunk sizing. These regions cover the first 45 MiB of the model.

## Saved chunks and winners

| Chunk | Source offset | Original bytes | Best archive bytes | Reduction | Brotli settings |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 0 | 15,728,640 | 9,584,841 | 39.06% | quality 11, lgwin 22 |
| 2 | 15,728,640 | 15,728,640 | 10,798,074 | 31.35% | quality 11, lgwin 22 |
| 3 | 31,457,280 | 15,728,640 | 12,169,749 | 22.63% | quality 11, lgwin 24 |

Original slices are `corpus_development/z-image-turbo/chunk_01.bin`, `chunk_02.bin`, and `chunk_03.bin`. Their selected archives are `chunk_01.best.ahwt`, `chunk_02.best.ahwt`, and `chunk_03.best.ahwt` in the same directory. `chunks.json` records source offsets, byte counts, dtype coverage, and SHA-256 values.

Chunk 1 includes 68,800 bytes of file framing/header, 4,183,104 bytes of F32 tensors, and 11,476,736 bytes of F8_E4M3 tensors. Chunks 2 and 3 contain F8_E4M3 tensor bytes throughout. These adjacent regions provide limited coverage of the full model.

The individually selected archives total **32,552,664 bytes**, a **31.01% reduction** from 47,185,920 bytes. Direct Brotli compression produced the smallest tested result for every chunk. The 24-bit window improved chunk 3 by 682 bytes and made the other two slightly larger. The standard 22-bit window is the smallest aggregate result when using one setting for all three.

## Full-chunk comparison

Every size includes its complete archive framing. AHPS and 7-Zip use their native containers. Other methods use a 43-byte experimental AHWT header plus the codec stream, including a stored SHA-256. The AHWT header specifies codec, transform, and original length. This format is decoded by `research.weight_compression`.

| Method | Chunk 1 bytes | Chunk 2 bytes | Chunk 3 bytes | Total bytes | Total encode seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw + brotli | 9,584,841 | 10,798,074 | 12,170,431 | 32,553,346 | 40.246 |
| raw + brotli24 | 9,588,781 | 10,798,551 | 12,169,749 | 32,557,081 | 46.649 |
| raw + zstd19 | 9,767,061 | 10,825,495 | 12,197,534 | 32,790,090 | 9.972 |
| raw + zstd22 | 9,767,196 | 10,825,495 | 12,197,533 | 32,790,224 | 10.137 |
| raw + zstd9 | 9,906,060 | 10,951,573 | 12,194,038 | 33,051,671 | 0.277 |
| raw + zstd3 | 10,049,478 | 10,820,564 | 12,193,984 | 33,064,026 | 0.085 |
| raw + xz9 | 9,697,527 | 11,014,147 | 12,358,687 | 33,070,361 | 14.259 |
| AHPS preset 9 + EXTREME | 9,698,196 | 11,017,036 | 12,364,416 | 33,079,648 | 20.400 |
| fp8_fields_full + brotli | 9,797,250 | 11,018,386 | 12,347,145 | 33,162,781 | 41.417 |
| 7-Zip PPMd order 6 / 256 MiB | 9,803,509 | 11,196,809 | 12,471,270 | 33,471,588 | 10.784 |
| raw + bz2 | 9,872,166 | 11,307,392 | 12,536,567 | 33,716,125 | 2.265 |
| bitplanes + brotli | 9,858,959 | 11,199,927 | 12,970,220 | 34,029,106 | 43.775 |

Zstandard level 3 produced 33,064,026 bytes, just **1.57% larger** than the individually selected minimum, in about **0.085 seconds total** in this observation. Direct Brotli with its 22-bit window took 40.246 seconds. AHPS produced 33,079,648 bytes and took 20.400 seconds. The smallest selected result is **1.59% smaller than AHPS**. Timing is one observation per cell on this machine, with cached local inputs, and does not establish sustained whole-model throughput.

## Search and validation

The screen tested six exact byte representations against four backends on a fixed 1 MiB sample from each chunk. Each sample concatenates 256 KiB slices at offsets 0, 5 MiB, 10 MiB, and the final 256 KiB. Representations were RAW, 64 KiB bit planes, whole-sample bit planes, 64 KiB FP8 bit-field grouping, whole-sample FP8 bit-field grouping, and delta followed by 64 KiB bit planes. Backends were XZ preset 9 + EXTREME, Zstandard 19, bzip2 9, and Brotli 11. All 72 screen cells completed.

The full comparison tested 11 methods on all three chunks. Two transformed candidates were retained from the screen to check their behavior at full size. A final three-cell follow-up tested Brotli quality 11 with lgwin 24. All 36 full-chunk cells completed. Each encoded archive was decoded and compared byte for byte with its source, with SHA-256 recorded. The chosen saved archives were decoded again after selection. The normal research CLI also restored chunk 1 successfully.

Each phase ran serially under the repository benchmark lock, with an 8 GiB committed-memory limit, a 300-second worker deadline, and a 900-second matrix deadline. Each chunk worker ran its methods sequentially. Peak-memory readings are cumulative across a worker and should not be compared as per-method memory measurements. The screening ledger hashes match the extraction manifest. Full workers validate their input size and SHA-256 against that manifest before encoding.

Eleven focused regression tests pass under the supplied Conda environment. They cover independent bit-packing fixtures, tails, 64 KiB boundaries, delta reset, all supported codecs, empty inputs, malformed headers, truncated streams, trailing data, and integrity failures. Validation identified and corrected an empty-frame validation issue in the experimental Zstandard reader. The final helper source hashes are recorded separately from the timed source snapshot.

Brotli and the tested transforms are isolated research tools. The existing AHPS format and production encoder are available as the comparison baseline. Results apply to these three chunks and the settings listed here.

## Reproduce and decode

The working environment is a Conda Python environment, Python 3.12.4, NumPy 2.4.5, zstandard 0.22.0, and brotli 1.0.9. The PPMd comparison used 7-Zip 26.00 with `-m0=PPMd -mo=6 -mmem=256m -mmt=1`.

Run from the repository root. The output path must be new.

```powershell
python -m research.weight_compression compress "corpus_development/z-image-turbo/chunk_01.bin" "chunk_01.ahwt" --codec brotli
python -m research.weight_compression compress "corpus_development/z-image-turbo/chunk_03.bin" "chunk_03.ahwt" --codec brotli --brotli-window 24
python -m research.weight_compression decompress "corpus_development/z-image-turbo/chunk_01.best.ahwt" "chunk_01.restored.bin"
```

The faster tested configuration uses `--codec zstd3`. The helper accepts at most 32 MiB per input and preserves every original byte.

Complete screen and full results are in weight-chunks.json (local record `weight-chunks.json`). Execution logs, all candidate archives, settings, and runtime hashes are under `experiments/weight-chunks-20261105/`. The reproducible matrix entry point is `python -m research.compare_weight_chunks`; existing experiment outputs are protected from overwrite.
