# Development benchmark summary

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Matrix complete for the supplied plan. Development evidence only; no research or product acceptance claim.

Selection comparison: step3-blender-timing-20261101. Explicit exhaustive reference: `{"chunk_trial_preset":null,"exe32_chunk_rival":true}`.

Times and peaks are medians [min–max]. Ratios divide the configuration median by the matching file/features exhaustive median; repetitions are not paired by ordinal. BCJ2 columns use the same file's BCJ2 median.

| File | Configuration | Rows / expected; failed rows | Status | Final bytes | Saved vs BCJ2 | Encode s | Decode s | Encode peak MiB | Decode peak MiB |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| blender | 7z-bcj2 | 3/3; 0 failed | VALID | 27,292,035 | +0.00% | 26.680 [26.676–27.566] | 0.619 [0.613–0.633] | 706.770 [706.754–706.805] | 77.922 [77.902–77.949] |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | 3/3; 0 failed | VALID | 26,380,608 | +3.34% | 86.901 [86.296–87.848] | 1.531 [1.522–1.546] | 1007.387 [1007.320–1007.395] | 383.602 [383.582–383.641] |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":null,"exe32_chunk_rival":true} | 3/3; 0 failed | VALID | 26,380,608 | +3.34% | 180.958 [178.444–181.780] | 1.563 [1.547–1.574] | 1007.430 [1007.395–1007.430] | 383.645 [383.641–383.676] |

| File | Configuration | Encode time / BCJ2 | Decode time / BCJ2 | Encode peak / BCJ2 | Decode peak / BCJ2 |
| --- | --- | ---: | ---: | ---: | ---: |
| blender | 7z-bcj2 | 1.000× | 1.000× | 1.000× | 1.000× |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | 3.257× | 2.474× | 1.425× | 4.923× |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":null,"exe32_chunk_rival":true} | 6.782× | 2.524× | 1.425× | 4.923× |

Selection versus explicit exhaustive with the same file and feature flags. Positive size delta means a larger archive.

| File | Configuration | Δ bytes vs exhaustive | Δ size % | Encode time / exhaustive | Encode time saved | Decode time / exhaustive | Encode peak / exhaustive | Decode peak / exhaustive |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":1,"exe32_chunk_rival":false} | +0 | +0.00% | 0.480× | +51.98% | 0.980× | 1.000× | 1.000× |
| blender | ahps exe32=1,exe_split=1,delta_split=1,per_chunk=1 search={"chunk_trial_preset":null,"exe32_chunk_rival":true} | +0 | +0.00% | 1.000× | +0.00% | 1.000× | 1.000× | 1.000× |
