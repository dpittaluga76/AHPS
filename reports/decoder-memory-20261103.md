# Incremental file-decoder memory experiment

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Result: **threshold not met**.

Completed correct round trips: 30/30. Correctness and frozen identities: pass.

API times include file close/flush and atomic replacement. Peak resident memory is the actual Python worker's kernel lifetime peak captured before streamed output verification. Launch-to-exit time, helper processes, and aggregate Job commitment remain separate in JSON.

No cache flush. Each input uses the fixed R/C, C/R, R/C, C/R, R/C order. R is the frozen reference and C is the candidate.

| Input | R API median [range], s | C API median [range], s | C/R median time | R peak median [range], MiB | C peak median [range], MiB | Median saving, MiB | Time gate | Memory gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| blender | 1.181120 [1.156231–1.201693] | 1.274501 [1.220671–1.294883] | 1.079062 | 140.558594 [140.554688–140.558594] | 137.703125 [137.691406–137.730469] | 2.855469 | fail | fail |
| mr | 0.114299 [0.112549–0.116506] | 0.115797 [0.114782–0.117404] | 1.013107 | 47.539062 [47.519531–47.554688] | 47.371094 [47.347656–47.378906] | 0.167969 | pass | pass |
| raw | 0.040590 [0.039339–0.042042] | 0.040313 [0.039142–0.061182] | 0.993168 | 52.062500 [52.035156–52.078125] | 51.199219 [51.156250–51.222656] | 0.863281 | pass | pass |

The fixed gates are: at least 8 MiB median Blender saving; candidate median API time at most 1.05 times reference for each input; MR/RAW median peak increase at most max(2 MiB, 5% of the reference median). Medians are computed independently from all five observations; paired ratios below are additional diagnostics.

blender: candidate API median must be ≤ 1.240176 s; saving must be ≥ 8.000000 MiB.

mr: candidate API median must be ≤ 0.120014 s; increase must be ≤ 2.376953 MiB.

raw: candidate API median must be ≤ 0.042620 s; increase must be ≤ 2.603125 MiB.

| Input | Pair | Order | R API, s | C API, s | Paired C/R | R peak, MiB | C peak, MiB | R−C, MiB | Outcome |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| blender | 1 | R/C | 1.156231 | 1.220671 | 1.055733 | 140.554688 | 137.730469 | 2.824219 | verified |
| blender | 2 | C/R | 1.178505 | 1.238166 | 1.050624 | 140.558594 | 137.691406 | 2.867188 | verified |
| blender | 3 | R/C | 1.201693 | 1.286023 | 1.070176 | 140.558594 | 137.703125 | 2.855469 | verified |
| blender | 4 | C/R | 1.189284 | 1.274501 | 1.071654 | 140.554688 | 137.710938 | 2.843750 | verified |
| blender | 5 | R/C | 1.181120 | 1.294883 | 1.096318 | 140.558594 | 137.691406 | 2.867188 | verified |
| mr | 1 | R/C | 0.114299 | 0.115232 | 1.008167 | 47.519531 | 47.378906 | 0.140625 | verified |
| mr | 2 | C/R | 0.112549 | 0.114782 | 1.019839 | 47.550781 | 47.355469 | 0.195312 | verified |
| mr | 3 | R/C | 0.116506 | 0.117404 | 1.007707 | 47.554688 | 47.347656 | 0.207031 | verified |
| mr | 4 | C/R | 0.113985 | 0.115797 | 1.015897 | 47.519531 | 47.378906 | 0.140625 | verified |
| mr | 5 | R/C | 0.114710 | 0.115968 | 1.010969 | 47.539062 | 47.371094 | 0.167969 | verified |
| raw | 1 | R/C | 0.042042 | 0.040606 | 0.965844 | 52.058594 | 51.156250 | 0.902344 | verified |
| raw | 2 | C/R | 0.040828 | 0.039142 | 0.958712 | 52.035156 | 51.218750 | 0.816406 | verified |
| raw | 3 | R/C | 0.040514 | 0.040224 | 0.992842 | 52.062500 | 51.171875 | 0.890625 | verified |
| raw | 4 | C/R | 0.039339 | 0.040313 | 1.024749 | 52.078125 | 51.222656 | 0.855469 | verified |
| raw | 5 | R/C | 0.040590 | 0.061182 | 1.507326 | 52.070312 | 51.199219 | 0.871094 | verified |

These are development measurements on two fixed existing archives and one synthetic RAW fixture. They support no compression-ratio, wider-corpus, stable timing, or sealed-evaluation claim.
