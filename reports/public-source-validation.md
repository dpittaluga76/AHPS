# Public source preparation and validation

This report describes the reviewed source export prepared from the current
working tree. It is not a published release or a claim that the original Git
history is suitable for public mirroring.

## Prepared material

- MIT license, contributor guidance, security reporting policy, and changelog.
- Public README, CLI/API usage, native build instructions, documentation index,
  and release/export instructions.
- Historical documentation reviewed for private paths, current-versus-historical
  claims, and references to local evidence that is not distributed.
- Python package metadata with an `ahps` console command and no third-party
  runtime dependencies in the core wheel.
- A source-only native builder, synthetic demo data generator, capability-aware
  test runner, and GitHub Actions configuration.
- Explicit public file inventory, redacting release checker, and source exporter
  producing a file-hash manifest and optional ZIP without Git metadata.

The source export includes no external corpus, model weights, local PDFs, CSV
datasets, compiled libraries, or raw benchmark logs. Four small synthetic v3
fixture pairs are required. The historical v3 DLL pair is optional local
evidence and excluded. In the v5 provenance JSON, three absolute runtime paths
were replaced by placeholders while retaining archive bytes and recorded hashes.

## Validation on Windows

All test runs below used the exported source copy, separate from the original
development tree and its private data. Test discovery is restricted to the root
test modules.

| Configuration | Tests | Passed | Skipped | Result |
| --- | ---: | ---: | ---: | --- |
| Source-only Python 3.11, no native libraries | 430 | 363 | 67 | Passed |
| Freshly compiled SIMD and rANS libraries | 430 | 392 | 38 | Passed |

Capability skips cover absent optional research codecs/binaries, native-only
checks in the Python run, the omitted historical DLL fixture, and hard-link
creation denied by the execution sandbox. The tests remain active when those
capabilities are available. No protection assertion was removed to obtain a
passing run.

Both native libraries compiled with GCC 16.2.0. Fresh subprocess checks confirmed
SIMD ABI 206 and rANS ABI 1. The core libraries were built from the exported C
sources. The original development DLLs were not replaced.

A Python wheel built successfully from the exported source and was installed
without dependencies into a separate Python 3.11 environment. Its `ahps selftest
--no-progress` command ran outside the checkout, used the Python backend, and
passed SHA-256 round-trip verification. The wheel contained only the five core
Python modules and package metadata/license files.

The documented synthetic corpus example also completed one native AHPS
encode/decode run successfully from the exported source. The record identified
the implementation as a source snapshot without inheriting the parent
repository's Git identity. That smoke test does not establish performance.

The public inventory passes file-type, size, path, selected credential-pattern,
and relative Markdown link checks. The final exporter rechecks the copied tree
and writes exact per-file SHA-256 hashes to `PUBLIC_RELEASE.json`.

## Evidence limits

GitHub Actions jobs are configured for Python-only Windows/Linux/macOS,
Linux native compatibility, optional research dependencies, and installed-wheel
verification. The hosted jobs have not run as part of this local preparation.
Linux/macOS native builds require their first hosted validation.

A focused credential scan found no matches in the reviewed historical text
blobs and working-tree text files. Binary/PDF contents and large historical
blobs were outside that scan. The public release is based on its explicit
reviewed inventory rather than an assertion that every development object is
safe to publish.

The original normal branch history includes roughly 211 MB of external corpus
data. Additional model/application/document artifacts occur in local checkpoint
refs. Excluding current files with `.gitignore` does not remove those objects.
Use the clean snapshot for a new public history as described in
[release preparation](../docs/releasing.md).

Detailed local test output, build products, and smoke-test records are retained
under `experiments/public-release-validation/` and are excluded from the source
bundle. Existing source history and local input data remain intact.
