# Changelog

## Unreleased

- Added exact CSV column grouping, selected only when its complete archive is
  smaller. Selected CSV archives use container v6.
- Added optional safetensors-aware F32 zero-bit elision and FP8 sign/magnitude
  rANS coding, with exact restoration and ordinary-AHPS fallback. Selected
  tensor archives use container v7 and currently accept at most 32 MiB per input.
- Added the optional portable native rANS kernel with a Python reference path.
- Avoided discarded chunk compression when a chunk has only one candidate.
  Eligible CSV/tensor final candidates can run concurrently with ordinary AHPS.
- Added throttled terminal progress and `--progress` / `--no-progress` controls.
- Added `bench --ahps-only`, retaining decompression and SHA-256 verification.
- Prepared source-only releases, MIT licensing, reproducible native builds,
  contributor documentation, and cross-platform CI configuration.

## Earlier development

The tool identifies itself as AHPS-Bin v2.6. This identifies the existing tool
generation, not a newly published release. Earlier work introduced v5 framing,
ABI 206, bounded decoding, atomic file restoration, per-chunk planning, and
preset-dependent search. Versions v3 and supported v4 variants remain readable.
Historical v2 archives require the legacy reader. See [the format contract](docs/format.md).

The dated reports record development observations. They are not claims of an
independent whole-corpus or whole-model evaluation. No tagged release is created
by this preparation work.
