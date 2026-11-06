# Using AHPS

Run these commands from the source directory with Python 3.11 or newer. A
standard Python build with `lzma` is required. `python ahps.py --help` and each
subcommand's `--help` list the supported options. An optional local package
installation provides an equivalent `ahps` command.

## Compression and restoration

```text
python ahps.py compress input.bin output.ahps
python ahps.py decompress output.ahps restored.bin
```

The output is an AHPS archive inside an XZ stream. General XZ tools can unpack
the inner container but cannot reconstruct the original input. Use the AHPS
decoder. The CLI's compression output path can overwrite an existing file.
Choose a distinct destination. File decompression replaces its destination only
after successful validation.

Compression currently loads the complete input into memory. Ordinary v4/v5
file decoding streams segments, while v3, CSV/v6, and tensor/v7 containers are
buffered. This is not a bounded-memory, multi-gigabyte model compressor.

```text
python ahps.py compress input.bin output.ahps --preset 3
python ahps.py --pure-python compress input.bin output.ahps
python ahps.py decompress output.ahps restored.bin --max-output-mb 512 --max-container-mb 513
```

Preset 9 enables LZMA EXTREME. Automatic selection uses preset 9 for inputs up
to 16 MiB and preset 3 above that threshold. Higher presets can spend more time
and memory. The global `--pure-python` option must precede the subcommand.
Decoder limits are documented in [the format contract](format.md).

## Single-file benchmarks

```text
python ahps.py bench input.bin
python ahps.py bench input.bin --ahps-only
python ahps.py bench input.bin --ahps-only --preset 9 --no-progress
```

The default compares AHPS with separate zlib/gzip-labelled and XZ baselines.
The legacy report label says gzip, but the baseline is `zlib.compress` and uses
zlib framing. Interpret its measured bytes accordingly. `--ahps-only` skips both
baseline runs. It retains AHPS encoding, decoding, and SHA-256 verification.
Displayed AHPS timing covers planning and encoding, not the entire command or
the subsequent verification. These one-shot timings are convenient checks, not
a controlled performance study.

Progress appears on interactive stderr. `--progress` forces it and
`--no-progress` disables it. Percentages track completed stages and existing
chunk/segment boundaries. During opaque native calls, the spinner continues
while the percentage holds. Redirected stderr stays quiet by default.

## CSV

```text
python ahps.py bench data.csv --csv --ahps-only
python ahps.py compress data.csv data.ahps --csv
python ahps.py decompress data.ahps restored.csv
```

CSV mode groups exact field spans by column and compares the final archive
against ordinary AHPS. It preserves original quoting, whitespace, escaping,
line endings, BOM, and embedded newlines. Unsupported inputs decline this
candidate. The supported parser expects commas, double-quote escaping, at least
two rows and columns, at most 1,024 columns and 1,000,000 fields, and a first
logical row within 64 KiB. Selected CSV candidates require a v6-capable reader.

Eligible inputs of at least 1 MiB on multicore systems can run the two final
compression candidates concurrently. This can reduce elapsed time while using
more memory. The smallest complete archive wins.

## Safetensors

```text
python ahps.py compress small-model.safetensors model.ahps --safetensors
python ahps.py bench chunk.bin --safetensors model.safetensors --source-offset 15728640 --ahps-only
python ahps.py decompress model.ahps restored.safetensors
```

The tensor mode currently accepts **at most 32 MiB per input**. For a complete
small safetensors file, pass `--safetensors` alone. For a raw slice, provide the
original model and the slice's absolute byte offset, including the source
header. The original source is consulted only for its header during encoding.
Decoding is self-contained. A raw chunk restores to the same raw chunk, not to
a complete model. The full original model is not automatically split or joined.

Eligible F32 spans omit verified zero lower bits and preserve the remaining
byte planes. F8_E4M3 spans use packed signs and per-tensor magnitude rANS coding.
Other supported dtypes and incomplete elements remain literal. No floating-point
rounding occurs. This candidate is selected only when its complete archive is
smaller. Selected tensor archives require a v7-capable reader.

## Python API

```python
from ahps import ahps_compress_bytes, ahps_decompress_bytes, ahps_decompress_file

data = b"repeated exact bytes\n" * 100
archive, report = ahps_compress_bytes(data, lzma_preset=3)
assert ahps_decompress_bytes(archive) == data
print(report.output_bytes)
# ahps_decompress_file("input.ahps", "restored.bin")
```

Use `csv_mode=True` for the CSV candidate. For tensors, obtain a validated layout
with `ahps_safetensors.parse_layout(path)` and pass it as `safetensors_layout`,
with `source_offset` for a slice. APIs are silent unless a caller supplies
`progress(fraction, stage)`. `force_python=True` selects the reference paths.

## Repeatable development benchmarks

`benchmark_ahps.py` is the Windows-oriented research harness. It uses explicit
manifest IDs, isolated encode/decode processes, complete archive sizes, and
append-only JSONL records. Its search defaults intentionally differ from the
product's automatic selection. See [search methods](search-methods.md).
Its AHPS measurements require a loaded native SIMD kernel and deliberately
refuse a silent Python fallback. Use `ahps.py bench` for portable Python-only
measurements. The example below assumes Windows with a compatible native build.

Create a small, synthetic example corpus and run only AHPS.

```text
python scripts/make_demo.py
python scripts/build_native.py
python benchmark_ahps.py --manifest examples/corpus_manifest.json --corpus-root . --files demo --codecs ahps --repeats 3 --output experiments/demo/results.jsonl
```

The example contains no downloaded corpus. Historical protocols refer to
external datasets and frozen local environments that are not part of the source
release. Read their prerequisites before trying to reproduce them.
