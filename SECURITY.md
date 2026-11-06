# Security

AHPS processes binary archives and may load optional native libraries. This is
experimental software and has not undergone an independent security audit.

## Reporting a vulnerability

If the hosting repository offers a private vulnerability-reporting action, use
it. Otherwise open an issue requesting a private contact route without posting
an exploit, sensitive archive, token, or personal data. No response-time or
support guarantee is currently offered.

Useful reports identify the affected revision, Python/native mode, platform,
archive version, reproduction steps, and observed impact. Prefer a minimal
synthetic reproducer. Explain whether the issue needs nondefault limits.

## Processing archives

The current decoder validates framing, sizes, transforms, and checksums, and
offers limits on LZMA memory, container bytes, and restored bytes. File decoding
uses a temporary destination and replaces the requested output only after
validation succeeds. These checks are not a process-level memory or CPU limit.
For untrusted inputs, run in an isolated process with operating-system resource
limits and choose appropriate decoder caps.

Checksums detect accidental corruption. They do not authenticate an archive or
its sender. AHPS provides no encryption.

Native libraries are loaded only from beside the corresponding Python module,
with ABI checks and CPU checks for the SIMD kernel. Only build or install
libraries from a source you trust. The source release excludes prebuilt binaries.
Use the global `--pure-python` flag before a subcommand to disable native paths.

Historical entry points and research codecs have separate formats and
validation contracts. Use `ahps.py` for the documented production-facing CLI.
See [format and decoder limits](docs/format.md) and [release preparation](docs/releasing.md).
