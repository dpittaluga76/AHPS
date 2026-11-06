# Compression search defaults

AHPS uses Candidate A (`FAST_CHUNKS`) automatically when the resolved final
compression preset is **9 with EXTREME**. Other resolved presets use
`EXHAUSTIVE`. Final compression still uses the resolved preset, and the
whole-file RAW fallback remains enabled.

| Public compression setting | Resolved final preset | Automatic search |
| --- | --- | --- |
| `--preset 9` / `lzma_preset=9` | 9 + EXTREME | Candidate A |
| Presets 0 through 8 | Requested level | Exhaustive |
| Preset omitted, input above 16 MiB | 3 | Exhaustive |
| Preset omitted, input at most 16 MiB | 9 + EXTREME | Candidate A, with chunk search inactive |

The chunk search runs only above 16 MiB when `per_chunk` is enabled. Candidate A
uses preset 1 to rank chunk plans and omits the extra whole-chunk EXE32 rival.
EXE32 remains available to the planner. Small inputs and disabled chunking use
the existing whole-file selection path. `CompressionReport.search` records the
resolved method even when chunk search is inactive.

For example, the CLI uses Candidate A for a large input with:

```text
python ahps.py compress input.bin output.ahps --preset 9
```

The Python API can override automatic selection explicitly:

```python
import ahps

archive, report = ahps.ahps_compress_bytes(
    data, lzma_preset=9, search=ahps.EXHAUSTIVE
)
```

`search=ahps.FAST_CHUNKS` explicitly selects Candidate A. `SearchMethod()` still
constructs exhaustive search, `search=None` is invalid, and the lower-level
`chunked_plan` function keeps its exhaustive default.

## Reproducing benchmark comparisons

`benchmark_ahps.py` always passes an explicit search method. Its omitted
`--search` option, or `--search '{}'`, selects **exhaustive**, including at
preset 9. This preserves the meaning of historical comparison rows; those rows
are not measurements of the product's new automatic default at preset 9.

To measure Candidate A, supply this JSON value to the harness's `--search`
option using the quoting rules for your shell:

```json
{"chunk_trial_preset": 1, "exe32_chunk_rival": false}
```

## Evidence and scope

The [Blender comparison](../reports/step3-search-results-overview.md) found the
same archive with 51.98% less median encoding time. At preset 9 extreme, the
[remaining development corpus](../reports/step3-development-a-overview.md)
produced thirteen identical archives and a Node archive 12,420 bytes smaller.
Nine of those inputs did not exercise chunk search.

The direct preset-3 Blender comparison produced an archive 12,760 bytes larger
with Candidate A. That ruled out a general default change; the maintainer then
approved the narrower preset-9-only policy. These are development observations,
not a guarantee of no size loss on other inputs. Decoder semantics, v5 format
and native ABI 206 are unchanged.
