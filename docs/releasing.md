# Preparing a public source release

The public source bundle is defined by `PUBLIC_FILES.txt`. It contains source,
documentation, CI configuration, and small synthetic fixtures. The exporter
copies current file contents and never copies `.git` or depends on Git history.
It refuses to overwrite an existing export or ZIP.

```text
python scripts/check_public.py
python scripts/export_public.py --output public-release/AHPS-Compressor --zip public-release/AHPS-Compressor-source.zip
```

The exporter generates `PUBLIC_RELEASE.json` inside each source bundle. It lists
relative file names, sizes, and SHA-256 hashes for that specific export; it is
not a tracked repository file. Review it before publishing. Run the documented
self-test and regression suite from inside the exported directory before
publishing. Build native libraries
from source only when needed. Remove locally generated build products from any
subsequent publication, or recreate the source export.

## History and data separation

The original development repository contains third-party corpus files in its
normal branch history. Their redistribution provenance is not established here.
Additional model weights, executables, and documents exist in local checkpoint
refs. A clean working-tree diff or `.gitignore` does not remove historical Git
objects. Do not mirror the development repository or publish its existing
history as a substitute for the reviewed source snapshot.

Keep the original development repository locally. To publish the clean snapshot
as a new Git repository, initialize Git **inside the exported directory**,
review the staged file list, and create a new initial commit. Add the intended
public remote only after reviewing its visibility and destination. This
preparation process does not create a remote repository, change visibility,
rewrite existing history, or push commits.

Excluded material includes model weights, external corpora, PDFs, CSV test
inputs, executables, compiled native libraries, raw benchmark logs/provenance,
the local corpus manifest, and unverified historical compiled-DLL fixtures.
Published Markdown reports retain historical measurements and explain the
availability limits of their underlying evidence. References to old commit IDs
are historical identifiers, not commits guaranteed to exist in the clean repo.

## Validation

The checker validates the allowlist, file bounds, fixture binary exceptions,
relative Markdown links, private home-directory paths, and selected credential
patterns. It reports locations rather than matching secret values. This is a
focused release check, not a guarantee that every possible secret is absent.
Review changes and provenance before each release.

The fixture JSON preserves historical archive bytes and hashes. Only the three
host-specific runtime path strings were replaced with portable placeholders.
The omitted v3 DLL pair remains optional local evidence. Required v3 fixtures
cover synthetic empty, arithmetic-record, PRNG, and tiny data.

## Packages and versioning

The current packaging version is `2.6.0`, corresponding to the existing tool
generation. The source documentation labels new work as unreleased. Creating
this snapshot does not create a release tag or publish a package.

With build dependencies installed, a local wheel can be built with
`python -m pip wheel . --no-deps --wheel-dir dist`. The wheel contains the five
core Python modules and has no third-party runtime dependency. It uses the
Python implementation unless compatible native libraries are installed beside
the modules. For native builds, use a source checkout and follow
[the build guide](building.md). Neither a PyPI project nor a package upload is
created by these commands.

The project source uses the [MIT license](../LICENSE). External datasets and
third-party applications are not covered by the project's license and are not
distributed by the source exporter.
