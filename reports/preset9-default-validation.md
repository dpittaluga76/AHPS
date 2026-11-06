# Preset-9-only default validation

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

The maintainer approved Candidate A as the automatic default only when the resolved
final preset is 9 + EXTREME. The core reviewer's core commit
`637485068e0b31b28e33dbab73110eabf2b2148e` implements that policy. The benchmark reviewer reviewed
the core, integrated the previously approved development evidence at `a27e41e`,
and added selection tests and [usage documentation](../docs/search-methods.md).

Validation on 2026-11-01:

- `python -B -m unittest discover -v`: **182 tests passed in 19.658 seconds**.
- Seven new tests cover exact preset/flag selection, the real 16 MiB boundary,
  explicit overrides, unchanged final compression settings, inactive-path
  reporting, lower-level exhaustive behavior, and a real archive round trip.
- Existing tests retain rejection of `search=None`, feature/search isolation,
  native/Python interoperability, decoder bounds, format fixtures and strict
  benchmark reporting.
- Independent static review of core, tests and documentation found no blocking
  issues. The withdrawn bundled shortcut `8c6c8f9` is not an ancestor of this
  integration.

The original integration log (local record `preset9-integration-tests-20261101.log`) is
preserved alongside this report.
No new corpus timing, fuzz campaign or holdout evaluation was run for this
selection-only integration. The previous [development results](step3-development-a-overview.md)
remain the compression evidence: preset 9 had no measured size losses, while
preset 3 lost 12,760 bytes on Blender and therefore keeps exhaustive search.

The benchmark harness still passes an explicit search method: omitted
`--search` or `{}` means exhaustive even at preset 9. Automatic product
selection is documented separately, and existing rows retain their meaning.
Decoder memory remains an open issue; this change does not address it.
