# Safetensors-aware AHPS compression

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

AHPS now offers an opt-in, lossless tensor representation through `--safetensors`. The encoder reads the original model's tensor boundaries and dtypes, constructs a v7 candidate, and compares its complete XZ archive against ordinary AHPS. The smaller result wins, with the ordinary result winning ties. Both buffered and file decoders understand v7 without the original model.

## Evidence from the supplied model

The source is `z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors`, 6,158,115,074 bytes. Its JSON header is 68,792 bytes and tensor data begins at absolute offset 68,800. The header declares 453 F32 tensors occupying 4,183,104 bytes and 209 F8_E4M3 tensors occupying 6,153,863,170 bytes.

The existing three test slices are consecutive **15 MiB** regions, each 15,728,640 bytes, starting at offsets 0, 15,728,640, and 31,457,280. They cover the first 45 MiB of the model. Chunk 1 contains the complete header and all F32 tensors, followed by FP8 weights. Chunks 2 and 3 contain only FP8 weights. These results do not establish whole-model compression performance.

Inspection found two useful structures.

- Every sampled F32 word, including the scale tensors, has its low 16 bits equal to zero. All 1,045,776 F32 words in this model occur in chunk 1. AHPS verifies the bits before omitting them, stores the two remaining byte planes, and reconstructs the missing bytes as zeros. This is exact bit preservation, including signed zero and special-value bit patterns. It performs no rounding or quantization.
- FP8 sign bits are close to evenly distributed, while magnitude codes are strongly biased and their distributions differ across tensors. AHPS packs the sign bits and uses a separate 128-symbol, 4096-total frequency table for each tensor intersection with a chunk. A new rANS encoder compresses those magnitudes. It accepts every magnitude code, so it does not depend on the sampled maximum or assume unseen values are absent.

The safetensors header, other supported dtypes, and incomplete F32 elements at slice boundaries remain literal. Each FP8 transform must be smaller than its literal payload including the frequency table. The complete candidate must then beat the ordinary AHPS archive. Existing XZ compression remains the final backend.

The format interpretation follows the [official safetensors specification](https://github.com/huggingface/safetensors#format). The implementation reads only the original source header to determine tensor locations. The actual input bytes determine every reversible transformation and frequency table.

## Complete archive results

All archive sizes include their framing. The earlier AHPS comparison and the new mode use preset 9 with EXTREME. The generic comparison selected the smallest tested Brotli archive separately for each chunk.

| Chunk | Original bytes | Previous AHPS bytes | Best previous generic bytes | Enhanced AHPS bytes | Encode seconds | Decode seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15,728,640 | 9,698,196 | 9,584,841 | 9,454,616 | 9.196 | 0.119 |
| 2 | 15,728,640 | 11,017,036 | 10,798,074 | 10,768,040 | 6.565 | 0.086 |
| 3 | 15,728,640 | 12,364,416 | 12,169,749 | 12,142,088 | 4.785 | 0.086 |
| Total | 47,185,920 | 33,079,648 | 32,552,664 | **32,364,744** | **20.546** | **0.291** |

The new mode saves 714,904 bytes (**2.16%**) against previous AHPS and 187,920 bytes (**0.58%**) against the best previous generic results. It reduces the original 45 MiB by **31.41%**. All three chunks selected the new tensor representation.

Timing is a single observation per chunk on this machine with cached local inputs, native rANS enabled, and both final candidates evaluated. Earlier AHPS encoding took 20.400 seconds total. These observations establish a compression improvement, without establishing an encoding speed improvement. Buffered decode timing excludes reading the archive from disk. Peak process working sets were approximately 391, 383, and 429 MiB. They include compression and verification activity.

The F32 transformation removes 2,091,552 zero bytes before the final backend. FP8 payloads, including their frequency tables, occupy 8,114,042, 10,767,699, and 12,141,390 bytes respectively. Chunk 1 benefits further from XZ compressing the retained header and F32 byte planes. Pure FP8 chunks leave little redundancy for that backend.

## Use the integrated feature

Run from the repository root to benchmark the second saved slice.

```powershell
python ahps.py bench "corpus_development/z-image-turbo/chunk_02.bin" --safetensors "z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors" --source-offset 15728640 --preset 9
```

Compress and restore the same slice through the normal AHPS commands.

```powershell
python ahps.py compress "corpus_development/z-image-turbo/chunk_02.bin" "chunk_02.tensor.ahps" --safetensors "z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors" --source-offset 15728640 --preset 9
python ahps.py decompress "chunk_02.tensor.ahps" "chunk_02.tensor.restored.bin"
```

For a complete safetensors file within the current 32 MiB limit, use `--safetensors` with no source argument. For raw slices, supply the source model and the slice's absolute byte offset. The original source is needed only when encoding. Use this updated decoder for v7 archives. Ordinary AHPS output remains compatible with its existing format.

## Scope and validation

The current tensor mode accepts inputs up to **32 MiB** and buffers a complete v7 container when decoding. It does not yet partition and stream a multi-gigabyte model automatically. Unknown or sub-byte dtypes are rejected by the layout parser. Only F32 and F8_E4M3 receive specialized transforms in this version. CSV and tensor modes are mutually exclusive.

`ahps_safetensors.py` implements validated source layouts and the v7 representation. `ahps_rans.py` supplies a standard-library reference coder and an optional native implementation in `ahps_rans.c`. Native and reference coding use the same wire format. The native library is optional, and `--pure-python` exercises the reference path. The DLL must match ABI version 1.

Each full-chunk worker checked its input length and SHA-256 against the extraction manifest, decoded the resulting archive through both public decoder APIs, and compared every restored byte. The runs were sequential under the repository benchmark lock, with 300-second worker deadlines and an 8 GiB job memory cap. Archives and raw worker records are in `experiments/safetensors-aware-20261105/`. Exact results and hashes are in safetensors-aware.json (local record `safetensors-aware.json`).

Regression coverage includes independent F32 and FP8 wire fixtures, all byte values, negative zero, partial sign groups, slice alignment, empty tensors, malformed source layouts, frequency tables, truncated streams, invalid states, trailing bytes, checksum failures, output limits, atomic file output, native pointer alias rejection, and the public CLI paths. The native library was rebuilt after review hardened its result-pointer validation. This change does not alter valid encoded bytes.

The final coordinated run passed **179 tests**. The real chunk 2 `bench` command reproduced the 10,768,040-byte archive and passed SHA-256 verification. The Python reference decoder independently restored all 15,728,640 bytes of chunk 3 from its saved native-produced archive. Final source and native hashes are recorded in `experiments/safetensors-aware-20261105/final-runtime.json`, separately from the timed snapshot. Run the relevant regression suite with this command.

```powershell
python scripts/run_tests.py test_ahps test_csv test_safetensors test_rans_native test_decode_limits test_decompress_file test_golden_archives test_search_defaults test_search_method test_plan_features test_poly2_canonical test_scorer_shortcut
```
