# Candidate A: remaining development corpus

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Subsequent decision: the maintainer approved the narrower preset-9-only automatic default, described in [search defaults](../docs/search-methods.md). The general-promotion failure and original evidence below remain unchanged.

The maintainer authorized testing every remaining development input and making Candidate A the default only if it shows no compression loss. The predeclared gate requires correct round trips and Candidate A complete archive bytes no larger than exhaustive on every file; gains cannot offset a regression.

One fresh exhaustive/A comparison was run on each of the 14 remaining manifest development entries: nine Silesia development files and five snapshotted application binaries. Both methods used frozen commit `06e1033e0aea0781f982076170ff3398d760d613`, final preset 9 extreme, block size 4096 and all features. Pair order alternated. This is size-only evidence; single-run timings and memory are retained as diagnostics, without a new speed claim.

| File | Exhaustive bytes | Candidate A bytes | A minus exhaustive | Size change | Same archive hash |
| --- | ---: | ---: | ---: | ---: | --- |
| dickens | 2,831,328 | 2,831,328 | +0 | +0.0000% | yes |
| mozilla | 13,309,548 | 13,309,548 | +0 | +0.0000% | yes |
| mr | 2,481,576 | 2,481,576 | +0 | +0.0000% | yes |
| nci | 1,449,096 | 1,449,096 | +0 | +0.0000% | yes |
| reymont | 1,315,432 | 1,315,432 | +0 | +0.0000% | yes |
| samba | 3,737,708 | 3,737,708 | +0 | +0.0000% | yes |
| sao | 4,425,420 | 4,425,420 | +0 | +0.0000% | yes |
| webster | 8,368,476 | 8,368,476 | +0 | +0.0000% | yes |
| x-ray | 3,999,468 | 3,999,468 | +0 | +0.0000% | yes |
| 7zip-cli | 181,784 | 181,784 | +0 | +0.0000% | yes |
| git-cli | 1,474,400 | 1,474,400 | +0 | +0.0000% | yes |
| node | 21,566,160 | 21,553,740 | -12,420 | -0.0576% | no |
| vlc | 301,440 | 301,440 | +0 | +0.0000% | yes |
| chrome | 1,628,460 | 1,628,460 | +0 | +0.0000% | yes |

**Preset-9 gate: PASS.** All 28 encode/decode pairs passed. 0 of 14 files have a larger archive with Candidate A.

The existing separate Blender cohort established byte-identical archives for exhaustive and A on that development file. It is not pooled into this frozen-commit matrix. Small files use the existing whole-file path, where the search option is inactive; equality there does not establish the quality of cheap chunk ranking. These development comparisons do not establish independent acceptance or behavior at every preset.

**Default promotion gate: FAIL.** The core reviewer identified that normal compression above 16 MiB resolves to preset 3. A separate direct Blender comparison at preset 3, with RAW fallback retained, produced 30,073,892 bytes for exhaustive and 30,086,652 bytes for A: a loss of 12,760 bytes (+0.0424%). Both round trips passed. This is a fresh same-commit comparison of A itself, independent of the withdrawn bundled shortcut. See `step3-blender-default-results.md` and its separate raw cohort.

At this checkpoint, Candidate A remained opt-in because the general no-loss condition failed. The later decision adopted Candidate A only for resolved preset 9 + EXTREME, as described at the top of this report. The preset-3 regression and its recorded measurements remain valid.

The nine inactive-path comparisons are dickens, mr, reymont, sao, x-ray, 7zip-cli, git-cli, vlc and chrome. They check ordinary round trips and unchanged whole-file behavior, not Candidate A chunk-search quality. The five active-path comparisons here are mozilla, nci, samba, webster and node.

No sealed input or reserve was opened or hashed. No Candidate B experiment ran. All attempted jobs are retained; no retries, failures or unstarted jobs occurred. Each of the three batches had its own 900-second watchdog and 300-second phase limits. Detailed planned results are in `step3-development-a-results.md`; raw rows, explicit plan, budget records and provenance are adjacent.
