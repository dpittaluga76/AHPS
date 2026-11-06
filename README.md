# AHPS Compressor

AHPS (Asymmetric Hybrid Program Synthesis) is an experimental lossless
compressor. It searches for reversible byte transforms, chooses candidates by
complete compressed size, and stores the result in an AHPS container inside XZ.
The core implementation uses Python's standard library, with optional C
acceleration for planning/transforms and FP8 entropy coding.

## Quick start

Use **Python 3.11 or newer** with `lzma` available. Download the reviewed source
bundle or clone the public source repository, then run from its directory.
No Python dependencies are required for the core CLI.

```text
python ahps.py selftest
python ahps.py compress input.bin output.ahps
python ahps.py decompress output.ahps restored.bin
python ahps.py bench input.bin --ahps-only
```

Use distinct input and output paths. Compression can overwrite its destination.
Decompression verifies the archive before replacing its destination. Ordinary
XZ tools cannot restore the original file from the inner AHPS container.

An optional local installation provides the `ahps` console command.

```text
python -m pip install .
ahps selftest
```

No package has been published to PyPI as part of this preparation. Build
instructions for the optional native libraries are in [docs/building.md](docs/building.md).
A source-only checkout runs with the Python implementation automatically.

## Features

| Capability | Behavior |
| --- | --- |
| Binary compression | Reversible arithmetic, byte-lane, and x86 transforms with final-size selection |
| CSV | Exact column grouping, including original quotes, line endings, and embedded newlines |
| Safetensors | Exact F32 zero-bit elision and FP8 magnitude coding using original tensor boundaries |
| Benchmarking | AHPS-only mode or separate comparison baselines, with SHA-256 round-trip verification |
| Progress | Percentage, stage, elapsed time, and spinner on interactive terminals |
| Restoration | Python/native decoding, declared-size limits, checksums, and atomic file output |

### CSV

```text
python ahps.py bench data.csv --csv --ahps-only
python ahps.py compress data.csv data.ahps --csv
python ahps.py decompress data.ahps restored.csv
```

The CSV representation is selected only when its complete archive beats ordinary
AHPS. Unsupported dialects fall back to ordinary compression. Selected archives
use container v6. Eligible final candidate trials may run concurrently, trading
additional memory for elapsed time.

### Safetensors

```text
python ahps.py compress small-model.safetensors model.ahps --safetensors
python ahps.py bench chunk.bin --safetensors model.safetensors --source-offset 15728640 --ahps-only
```

Tensor mode currently accepts **up to 32 MiB per input**. Complete larger models
must be sliced externally. For raw slices, provide the original source header
and the absolute slice offset. Restoration needs only the resulting archive.
Selected tensor representations use container v7 and preserve every original
bit. No model quantization or numerical approximation is performed.

### Benchmarks and progress

```text
python ahps.py bench input.bin
python ahps.py bench input.bin --ahps-only --preset 9
python ahps.py bench input.bin --ahps-only --no-progress
```

`--ahps-only` skips the separate comparison encoders while retaining AHPS
compression, decoding, and SHA-256 verification. The ordinary comparison's
legacy gzip label refers to `zlib.compress` output with zlib framing.

Progress is automatic on interactive stderr. `--progress` forces it and
`--no-progress` disables it. It updates at most four times per second and uses
existing processing boundaries. Percentages track workflow stages, not remaining
time. Opaque native calls hold the percentage while the spinner remains active.
The codec calls, parameters, and archive bytes are unchanged by the display.

## Status and limits

This is a research-oriented compressor with a usable CLI, not a demonstrated
universal improvement over established codecs. Compression currently buffers
the complete input and may test multiple candidates. Large-file memory use and
search time can be substantial. CSV and tensor decoding also buffer their
containers. SIMD acceleration requires supported x86-64 hardware with AVX2;
rANS has a separate portable native implementation. Reference Python paths are
available with the global `--pure-python` option before the subcommand.

The current reader accepts v3, supported v4 variants, v5, v6, and v7. Historical
v2 archives use the legacy reader. Older readers cannot decode newly selected
CSV or tensor representations. See [the format contract](docs/format.md).

The recorded CSV, tensor, binary, and research results use development inputs.
The three tensor samples cover only the first 45 MiB of one model. No independent
whole-model or final whole-corpus evaluation is claimed. Performance numbers are
specific to their recorded inputs, settings, and environment.

## Development

```text
python scripts/run_tests.py
python scripts/check_public.py
```

The default tests use synthetic data and small public fixtures. Tests requiring
native libraries, Windows-specific research components, or unavailable
filesystem capabilities report explicit skips. Optional external weight-codec
experiments use `requirements-research.txt` and are separate from core AHPS.

The repository contains three layers. `ahps.py` and the `ahps_*` support modules
are the current core. `app.py`, `ahps-binv2.py`, and `ahps-binv2_benchmark.py` are
historical prototypes retained for context and compatibility. `research/`
contains separate experimental codecs and fixed development protocols.

## Documentation

- [Usage and Python API](docs/usage.md)
- [Native builds](docs/building.md)
- [Format and decoder limits](docs/format.md)
- [Documentation and research index](docs/index.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)
- [Changes](CHANGELOG.md) and [source release preparation](docs/releasing.md)
- [Public source validation](reports/public-source-validation.md)
- [CSV results](reports/csv-compression.md), [tensor results](reports/safetensors-aware.md), and [progress validation](reports/terminal-progress.md)

The public source snapshot excludes external corpora, model weights, personal
files, compiled libraries, raw local logs, and development Git history. Historical
reports explain which evidence is available. `PUBLIC_FILES.txt` defines the
reviewed source inventory.

## License

Project source and documentation are available under the [MIT license](LICENSE).
External data and third-party applications are not included in that license or
the public source bundle.
