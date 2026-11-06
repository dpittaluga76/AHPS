# Development benchmark summary

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Matrix complete for the supplied plan. Development evidence only; no research or product acceptance claim.

Size-only view. Valid groups require hash-verified round trips; time and memory are not evaluated.

| File | Configuration | Rows / expected; failed rows | Status | Final bytes | Saved vs BCJ2 |
| --- | --- | --- | --- | ---: | ---: |
| dickens | 7z-bcj2 | 1/1; 0 failed | VALID | 2,831,174 | +0.00% |
| dickens | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 2,831,328 | -0.01% |
| mozilla | 7z-bcj2 | 1/1; 0 failed | VALID | 13,492,757 | +0.00% |
| mozilla | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 13,309,548 | +1.36% |
| mr | 7z-bcj2 | 1/1; 0 failed | VALID | 2,750,209 | +0.00% |
| mr | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | 1/1; 0 failed | VALID | 2,615,300 | +4.91% |
| mr | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 2,481,576 | +9.77% |
| nci | 7z-bcj2 | 1/1; 0 failed | VALID | 1,741,473 | +0.00% |
| nci | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 1,449,096 | +16.79% |
| reymont | 7z-bcj2 | 1/1; 0 failed | VALID | 1,318,482 | +0.00% |
| reymont | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 1,315,432 | +0.23% |
| samba | 7z-bcj2 | 1/1; 0 failed | VALID | 3,762,012 | +0.00% |
| samba | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=0 | 1/1; 0 failed | VALID | 3,737,708 | +0.65% |
| samba | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 3,737,708 | +0.65% |
| sao | 7z-bcj2 | 1/1; 0 failed | VALID | 4,415,699 | +0.00% |
| sao | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | 1/1; 0 failed | VALID | 4,425,420 | -0.22% |
| sao | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 4,425,420 | -0.22% |
| webster | 7z-bcj2 | 1/1; 0 failed | VALID | 8,388,915 | +0.00% |
| webster | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 8,368,476 | +0.24% |
| x-ray | 7z-bcj2 | 1/1; 0 failed | VALID | 4,480,482 | +0.00% |
| x-ray | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | 1/1; 0 failed | VALID | 4,252,028 | +5.10% |
| x-ray | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 | 1/1; 0 failed | VALID | 3,999,468 | +10.74% |

Ablations versus all-enabled AHPS on the same file; positive byte delta means a larger archive.

| File | Configuration | Δ archive bytes |
| --- | --- | ---: |
| mr | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | +133,724 |
| samba | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=0 | +0 |
| sao | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | +0 |
| x-ray | ahps exe32=1,exe_split=1,delta_split=0,per_chunk=1 | +252,560 |
