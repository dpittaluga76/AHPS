# Development benchmark summary

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Matrix complete for the supplied plan. Development evidence only; no research or product acceptance claim.

Times and peaks are medians [min–max]. Ratios divide the configuration median by the same file's BCJ2 median; repetitions are not paired by ordinal.

| File | Configuration | Rows / expected; failed rows | Status | Final bytes | Saved vs BCJ2 | Encode s | Decode s | Encode peak MiB | Decode peak MiB |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| blender | 7z-bcj2 | 3/3; 0 failed | VALID | 27,292,035 | +0.00% | 28.424 [27.676–28.614] | 0.628 [0.610–0.636] | 708.441 [708.418–708.465] | 79.488 [79.480–79.520] |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | 3/3; 0 failed | VALID | 26,380,608 | +3.34% | 90.837 [90.324–97.788] | 1.499 [1.491–1.555] | 1003.531 [1003.527–1003.531] | 273.086 [273.086–273.086] |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} decode=file | 3/3; 0 failed | VALID | 26,380,608 | +3.34% | 90.417 [87.865–93.017] | 1.473 [1.467–1.475] | 1003.531 [1003.527–1003.535] | 139.949 [139.945–139.949] |

| File | Configuration | Encode time / BCJ2 | Decode time / BCJ2 | Encode peak / BCJ2 | Decode peak / BCJ2 |
| --- | --- | ---: | ---: | ---: | ---: |
| blender | 7z-bcj2 | 1.000× | 1.000× | 1.000× | 1.000× |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | 3.196× | 2.387× | 1.417× | 3.436× |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} decode=file | 3.181× | 2.346× | 1.417× | 1.761× |
