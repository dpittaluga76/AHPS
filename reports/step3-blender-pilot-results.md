# Development benchmark summary

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Matrix complete for the supplied plan. Development evidence only; no research or product acceptance claim.

Selection comparison: step3-blender-pilot-20261101. Explicit exhaustive reference: `{"chunk_trial_preset":null,"exe32_chunk_rival":true}`.

Size-only view. Valid groups require hash-verified round trips; time and memory are not evaluated.

| File | Configuration | Rows / expected; failed rows | Status | Final bytes |
| --- | --- | --- | --- | ---: |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | 1/1; 0 failed | VALID | 26,380,608 |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":true} | 1/1; 0 failed | VALID | 26,457,052 |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":null,"exe32_chunk_rival":true} | 1/1; 0 failed | VALID | 26,380,608 |

Selection versus explicit exhaustive with the same file and feature flags. Positive size delta means a larger archive.

| File | Configuration | Δ bytes vs exhaustive | Δ size % |
| --- | --- | ---: | ---: |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | +0 | +0.00% |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":true} | +76,444 | +0.29% |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":null,"exe32_chunk_rival":true} | +0 | +0.00% |
