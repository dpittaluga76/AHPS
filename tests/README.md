# Tests

Run tests from the repository root with Python 3.11 or newer. The core suite
uses the standard library, synthetic inputs, and the public fixtures in this
directory.

| Location | Purpose |
| --- | --- |
| `test_*.py` | Codec, CLI, benchmark, research, and release-tool regression tests |
| `test_support.py` | Shared capability checks and their tests |
| `fixtures/v3/` | Preserved historical archives and restored-data hashes |
| `fixtures/v5/` | Frozen default-feature provenance and reference data |

## Run the suite

```text
python scripts/run_tests.py
```

The runner limits discovery to this test package. Missing optional native
libraries, research dependencies, or platform capabilities produce explicit
skips. External corpora and model weights are not needed for the core suite.

For a source-only checkout, require the Python implementations:

```text
python scripts/run_tests.py --require-pure-python -v
```

After building both native libraries on a compatible host, require them to load:

```text
python scripts/build_native.py --target all
python scripts/run_tests.py --require-native -v
```

`--require-native` fails unless both libraries load; hosts that cannot build the
SIMD kernel can use the default test command. See the
[native build guide](../docs/building.md) for platform requirements.

## Run focused tests

Selectors can name a module, class, or method:

```text
python scripts/run_tests.py tests.test_csv -v
python scripts/run_tests.py tests.test_csv.CsvTransformTests -v
python scripts/run_tests.py test_csv -v
```

The runner accepts both package-qualified names and the historical short
module names. Standard `unittest` commands use package-qualified names:

```text
python -m unittest tests.test_csv -v
python -m unittest discover -s tests -t . -v
```

## Add or update tests

Place new modules under `tests/` using the `test_*.py` naming convention. Import
shared helpers from `tests.test_support`. Keep generated data in temporary
directories and add new public source files to `PUBLIC_FILES.txt`.

Preserved fixtures are independent compatibility evidence. Do not regenerate
archives or change recorded hashes merely to make a test pass. See the
[v3 fixture notes](fixtures/v3/README.md) and
[v5 fixture notes](fixtures/v5/README.md) for their provenance and limits.
