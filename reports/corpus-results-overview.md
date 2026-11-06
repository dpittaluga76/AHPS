# Short development-corpus result

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

All **22 encode/decode pairs passed SHA-256 round-trip checks**, finishing within
**421.2 seconds of the original batch start** and its 900-second ceiling.
There were no failures, missing pairs or retries. Only the nine development
Silesia files were read; `ooffice`, `osdb`, `xml` and application holdouts remain
sealed. Each configuration ran once, so this report makes size/correctness
claims only, not stable timing or memory comparisons.

Default AHPS produced **41,918,052 bytes** against BCJ2's **43,181,203 bytes**:
**2.93% less total archive data**, with smaller archives on **7 of 9 files**.

| File | AHPS bytes | BCJ2 bytes | AHPS saving |
| --- | ---: | ---: | ---: |
| dickens | 2,831,328 | 2,831,174 | -0.01% |
| mozilla | 13,309,548 | 13,492,757 | +1.36% |
| mr | 2,481,576 | 2,750,209 | +9.77% |
| nci | 1,449,096 | 1,741,473 | +16.79% |
| reymont | 1,315,432 | 1,318,482 | +0.23% |
| samba | 3,737,708 | 3,762,012 | +0.65% |
| sao | 4,425,420 | 4,415,699 | -0.22% |
| webster | 8,368,476 | 8,388,915 | +0.24% |
| x-ray | 3,999,468 | 4,480,482 | +10.74% |

## What the component checks establish

| Change | File | Changed archive bytes | Increase from default |
| --- | --- | ---: | ---: |
| Disable delta-to-SPLIT | mr | 2,615,300 | 133,724 bytes / 5.39% |
| Disable delta-to-SPLIT | x-ray | 4,252,028 | 252,560 bytes / 6.31% |
| Disable delta-to-SPLIT | sao | 4,425,420 | 0 bytes |
| Disable per-chunk selection | samba | 3,737,708 | 0 bytes |

Delta-to-SPLIT has a measurable size benefit on MR and X-ray under this fixed
implementation. SAO selected whole-file RAW with either setting. Samba also
selected whole-file RAW in both cases, so the chunking comparison did not
exercise a winning chunked transform plan. **Chunking's contribution remains
unresolved**; the zero difference here does not establish that it can be removed.

Six default winners were RAW-only: dickens, nci, reymont, samba, sao and webster.
RAW still uses LZMA compression, but applies no AHPS preprocessing. In particular,
NCI's 16.79% advantage cannot be attributed to AHPS transforms. A pinned plain-XZ
control would be needed to separate backend/settings/container effects. None of
the nine winning plans used EXE32 or EXE_SPLIT, so this batch does not establish
their value on executable data.

## Reproduction and limits

All comparisons use commit `4080e23ffe134b03266ece7f5c519ced4e53523d`, native ABI
206, block size 4096, AHPS preset 9 extreme, and 7-Zip 26.00 BCJ2 with
`-mx=9 -md=64m -mf=BCJ2 -mmt=1 -ms=off`. Every BCJ2 file-entry method was verified.
The four follow-ups reused the exact default rows from the same implementation
and source hashes; they remained within the original overall deadline.

`corpus-results.md` contains the validated complete 22-job table and component
deltas. The adjacent JSONL, plan and budget files preserve all measurements,
hashes, settings, exact commands and outcomes. The reporter ran in `--size-only`
mode and checked completeness against `corpus-components-plan.json`.

This bounded development batch does not complete the broader component and
interaction gate. A separate bounded Blender-only follow-up now covers one
EXE_SPLIT removal, an informative per-chunk removal and the predicate experiment;
see `blender-components-overview.md`. Remaining removals/interactions, RAW/BCJ
controls, broader component applicability, search-cost work and the final frozen
evaluation remain outstanding. No new run
is queued. Earlier approximate tuned-XZ results are not used as controls here.
