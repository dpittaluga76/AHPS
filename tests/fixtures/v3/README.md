# Historical v3 golden archives

These preserved fixtures were reportedly generated before v2.4, using the C
encoder (`*_c.ahps`) and Python encoder (`*_py.ahps`). Their original build
provenance has not been independently verified.

The public source export contains four synthetic cases, `empty`, `lcg`, `struct`,
and `tiny`, comprising eight archives and four `.sha256` sidecars. Each sidecar
records the SHA-256 of the expected restored data. The C and Python archive pair
for each case has identical bytes. All contain the historical `AHPS\x03` header.

The optional local `dll` pair restores a compiled executable whose exact build
provenance remains unknown. Its two archives and sidecar are excluded from the
public source export. If installed locally, all three files must be present and
the tests verify them. An incomplete optional set fails validation.

| Archive pair | SHA-256 of each preserved archive |
| --- | --- |
| `empty_c.ahps`, `empty_py.ahps` | `21d6704ef2c6d63fedbf8548bcb6e4f4570f76500d39570302cb51bd5c240080` |
| `lcg_c.ahps`, `lcg_py.ahps` | `0dd879def0d4c14f3c1abab51a0d49d8e59c0ca00301a281ffae0c8c7fe2d8dc` |
| `struct_c.ahps`, `struct_py.ahps` | `08ac522d398085b90d3b40950d6bb17a5e03067f7ebc00076cf90bb9da05da11` |
| `tiny_c.ahps`, `tiny_py.ahps` | `ba1dee68ad8adc1d60ee702deca754f7105e30f6c9595c4405c1292eb5b49c2e` |

The optional `dll` archives each have SHA-256
`caf5ed4104d253268c7958d235dab9a31dccdaec45081916d2f6d2578d7257b0`.

`python -m unittest tests.test_golden_archives -v` verifies the v3 header with bounded
XZ extraction, then checks restored-data hashes for every archive through both
decoders. The native decoder test explicitly skips when the C kernel is absent.
The optional historical DLL test explicitly skips when all three local files
are absent. The small fixture size and decoded-container bounds are checked
before decompression.
