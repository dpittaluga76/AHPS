# Dependability validation, 2026-10-31

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Production changes reviewed through `02e8736`; integrated with the benchmark reviewer's tests
on the recorded dependability branch. The new writer uses `AHPS\x05`, native ABI 206,
and the contract in `docs/format.md`. Root `xchange.txt` records cross-review.

## Changes verified

- Native/Python known-output EXE_SPLIT vectors, instruction cutoffs, ModRM
  exclusions, RIP-relative forms, address wrapping, chained transforms, and
  valid CRC-bearing crafted containers.
- Ten preserved v3 archives decode to their recorded SHA-256 values on both
  engines. Literal v4 fixtures remain independent of the current writer magic.
- A hand-derived earlier four-lane EXE_SPLIT fixture fails with the documented
  unsupported-legacy/integrity error; the reader does not try another decoder.
- Separate liblzma working-memory, cumulative container and restored-output
  limits; exact boundaries; early header rejection; invalid limit configuration;
  strict single-XZ-stream decoding; unsupported-CPU fallback before DLL loading.
- Writer identity is independently asserted as v5/ABI 206. Mock libraries with
  old ABI 205 or an unknown future ABI are rejected before native initialization.
- The legacy v2 decoder now explicitly uses the encoder's historical 64 MiB
  dictionary. Its regression contains a genuine match beyond 8 MiB and proves
  that an 8 MiB decoder fails. The historical format remains unchanged.
- The x86 rival test measures all actual finalist sizes rather than requiring a
  particular winning opcode. Structural-negative fixtures carry correct CRCs
  where applicable, so checksum rejection cannot conceal parser mistakes.

## Executed checks

`python -B -m unittest discover`: 91 tests passed in the final integrated run,
including the existing census controls and 13 synthetic benchmark-harness
tests. Its measurements are not yet corpus results. The extra harness tests
prevent reads/writes through accidental sealed-input or hardlink aliases.

`fuzz_ahps.py` runs workers in disposable subprocesses. Wrong output, engine
disagreement, unexpected exceptions, accepted known-invalid input, native
crashes and timeouts fail the run. Seed/index pairs reproduce individual cases.
Mutants are bounded by 16 MiB liblzma memory and 1 MiB container/output caps.

| Campaign | Cases | Result | Report |
| --- | ---: | --- | --- |
| Full pipeline, crafted valid, malformed, transform, mutations | 1,000 each | All five passed | `fuzz-step1-final.json` |
| EXE_SPLIT forward/inverse and arbitrary residuals | 60,000 | Native/Python agreement and inversion passed | `fuzz-exesplit60000.json` |
| Deliberately malformed v3/v4/v5 | 21,000 | 7,000 per version; all safely rejected | `fuzz-malformed-all-versions.json` |
| Byte mutations of generated v3/v4/v5 and historical v3 fixtures | 10,000 | 2,227 accepted, 7,773 rejected; identical engine outcomes | `fuzz-mutations10000.json` |

Accepted v4/v5 mutants also pass an independent wire-header length/CRC check.
Reports contain code/library hashes, environment, seed, batch counts and
coverage. The 60,000-transform report predates expansion of the harness to v3
mutations; production code hashes identify the implementation actually tested.

## Limits of this evidence

These are bounded tests, not a proof of memory safety. They do not establish
cross-platform behavior. The fallback startup seam was mocked on this AVX2
Windows host. Fuzz payloads are small; large-input planning is covered by
synthetic tests with lowered thresholds, not by this fuzz campaign.

No performance conclusion follows from these runs. No sealed Silesia or new
x86 holdout contents were read. Older four-lane v4 EXE_SPLIT archives remain
unsupported; recover them using the producing version and then re-encode.
The legacy v2 fix addresses its dictionary mismatch, not a full security review
of that historical decoder.
