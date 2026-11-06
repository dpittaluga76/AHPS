# Contributing

AHPS is an experimental lossless compressor. Contributions should preserve exact
restoration and make their compression, speed, and memory tradeoffs measurable.

Use Python 3.11 or newer with the standard-library `lzma` module. The production
Python implementation has no third-party Python dependencies. Follow
[the build guide](docs/building.md) for optional native acceleration.

Run these commands from the repository root:

```text
python ahps.py selftest --no-progress
python scripts/run_tests.py
python scripts/run_tests.py tests.test_csv -v
python scripts/check_public.py
```

Tests and shared helpers live in `tests/`, with preserved reference data in
`tests/fixtures/`. Add new regression modules as `tests/test_*.py`; the runner
finds them automatically. A focused selector can name a module, class, or method,
for example `tests.test_csv.CsvTransformTests`. Short selectors such as
`test_csv` also work through `scripts/run_tests.py`.

Tests use synthetic inputs and the small public fixtures. Native-only and
platform-specific tests report capability skips when their requirements are
unavailable. Optional weight-comparison experiments use
`requirements-research.txt`. External corpora and model files are not required
to contribute to the core codec. See the [test guide](tests/README.md) for
Python/native checks and standard `unittest` discovery, and the
[repository layout](README.md#repository-layout) for where other changes belong.

Before proposing a change, describe the input or failure that motivates it.
For codec changes, include independent wire fixtures or a focused regression
that would fail before the fix, and test both Python and native implementations
when affected. Check corrupted input, truncation, resource limits, and the file
decoder's atomic output behavior when relevant.

Changing the serialized representation requires a new archive version and an
update to [the format contract](docs/format.md). Changing a native interface or
its semantics requires its ABI version to change. Planner changes can retain a
format only when existing decoders restore the same bytes. Do not regenerate
frozen reference archives merely to make tests pass.

Performance results should state hardware, Python/compiler versions, input
provenance, settings, complete archive sizes, timing boundaries, repetitions,
and verification. Run comparisons serially. Distinguish development inputs
from independent evaluation data, and report regressions and inconclusive
results. See [the evaluation protocol](reports/evaluation-protocol.md).

Keep generated archives, model weights, PDFs, third-party corpora, credentials,
native binaries, and machine-specific logs out of changes. Public release files
are explicitly listed in `PUBLIC_FILES.txt`. New source, tests, or documentation
intended for the source release must be added there and checked with
`python scripts/check_public.py`.

Pull requests should explain the resulting behavior, relevant validation, and
remaining limitations. Contributions are made under the project's [MIT license](LICENSE).
