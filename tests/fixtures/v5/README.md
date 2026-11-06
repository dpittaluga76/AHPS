# Default-feature reference archives

Public-source note: the three original absolute runtime paths in the JSON were
replaced with `<python-runtime>` placeholders. All recorded archive bytes,
hashes, sizes, settings, timestamps, and source identities are unchanged.
Historical commit identifiers refer to the original development repository.

`default-features.json` records four independent archives from commit `1d436f33090276b64a841709393833f4e15bef27`, captured after the baseline runner was released and before the feature implementation was merged.

The parent process obtained `ahps.py`, `ahps_simd.c` and **the checked-in `ahps_simd.dll`** directly with `git show`. The DLL was not rebuilt. An isolated child loaded that source and DLL from a temporary directory, checked the loaded paths and ABI 206, and imported no candidate feature implementation. The child verified every archive with both frozen decoders, then exited before the parent removed the temporary directory.

The JSON records the full commit/tree, each source and DLL git blob ID and SHA-256, runtime executable and `_lzma` extension SHA-256, Python version, capture timestamp, fixture hash, exact settings, archive bytes/hex/hashes, container hashes and decoder verification hashes.

The synthetic input is 8,449 bytes: 4,096 bytes of deterministic CALL/RIP-relative records, 256 fixed 16-byte numeric records, then a 257-byte arithmetic suffix. Its generator is `make_fixture_data()` in `tests/test_plan_features.py`; the capture used a copied standalone generator without importing that test module or candidate `ahps.py`.

Input SHA-256: `7fa07e4edcf0adaf2837c979a610c390bf2ba8f18627fa3e75353c5ba21f0fce`.

| Encoder | Planner case | Archive bytes |
| --- | --- | ---: |
| Python | Whole-file default thresholds | 332 |
| Native checked-in DLL | Whole-file default thresholds | 332 |
| Python | Forced chunk path | 280 |
| Native checked-in DLL | Forced chunk path | 280 |

All cases use block size 512 and explicit LZMA preset 0. The forced chunk case changes only `EXTREME_LIMIT` to 1,024 and `PLAN_CHUNK_LEN` to 4,096; the JSON includes effective constants. Capture used CPython 3.12.14 and the exact runtime artifacts recorded there. Eight frozen-decoder round trips passed, two for each archive.

These fixtures check byte identity under the recorded compressor and liblzma environment. A runtime change can legitimately change compressed bytes without changing the decoded format; review such a change explicitly. Never regenerate expected output from a candidate merely to make a test pass, and never generate references during test execution. No corpus or sealed data was used.
