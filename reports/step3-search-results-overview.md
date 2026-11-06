# Step 3: bounded Blender search comparison

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

Candidate A reduced median encoding time by **51.98% (2.08x faster)** on Blender while producing exactly the same complete archive as exhaustive search.

All three pilot pairs and nine fresh timing pairs passed SHA-256 round-trip verification. No failures, retries or missing jobs occurred. These are development measurements on one previously used executable, not independent x86 acceptance results.

## What changed

The unchanged default is `SearchMethod()` (exhaustive). Candidate A is `SearchMethod(chunk_trial_preset=1, exe32_chunk_rival=False)`: use preset 1 to rank chunk plans and omit the extra whole-chunk EXE32 rival. EXE32 remains available in scoring/refinement and planner-produced plans. Final compression stays at preset 9 extreme; the whole-file RAW fallback remains enabled. Decoder semantics and archive format are unchanged.

## Separate size-only pilot

| Search method | Complete archive bytes | Increase over exhaustive |
| --- | ---: | ---: |
| Exhaustive | 26,380,608 | 0 |
| Cheap trials only, EXE32 rival retained | 26,457,052 | 76,444 / 0.290% |
| Candidate A | 26,380,608 | 0 |

All pilot cases used the same implementation and retained RAW fallback. The cheap-only size penalty is observed; its cause being lost matches across chunk boundaries remains an unproven explanation. Pilot timings do not enter the repeated timing cohort.

## Fresh three-repetition timing cohort

| Method | Archive bytes | Median encode seconds [range] | Median decode seconds [range] | Median encode peak MiB | Median decode peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exhaustive | 26,380,608 | 180.958 [178.444-181.780] | 1.563 [1.547-1.574] | 1007.43 | 383.64 |
| Candidate A | 26,380,608 | 86.901 [86.296-87.848] | 1.531 [1.522-1.546] | 1007.39 | 383.60 |
| BCJ2 | 27,292,035 | 26.680 [26.676-27.566] | 0.619 [0.613-0.633] | 706.77 | 77.92 |

Candidate A is **3.34% smaller than BCJ2**, with **3.257x its median encoding time** and **4.923x its median decoding peak working set** in this cohort. Decoder memory remains a separate cost; no decoder optimization was made. The proposed final product conditions concern a geometric mean over independent files and are not evaluated by this one development file.

For this file, the 3.257x encoding ratio remains just above the working 3x time target, and the 4.923x decoding peak remains above the working 2x memory target. The relevant BCJ2 median is this cohort's 26.680 seconds. The historical roughly 28.7-second median belongs to another cohort and is excluded from these comparisons.

Every AHPS archive in the timing cohort has SHA-256 `975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd`. The speed comparison uses ratios of per-method medians, with all raw repetitions and ranges retained.

## Protocol and evidence

All measured runs used frozen commit `58b80588a6dbdb21dbd839caab89322c85dde827`, native ABI 206, block size 4096, all four features enabled, and the pinned Python/liblzma/7-Zip identities recorded in JSONL. BCJ2 used 7-Zip 26.00 with `-mx=9 -md=64m -mf=BCJ2 -mmt=1 -ms=off`; its actual archive entry was verified.

Each codec used fresh encode and separate decode processes, measured from process launch to exit; memory is kernel peak working set. No cache flush; decode follows encode. No other benchmark, tests or builds ran concurrently. Ordinary OS/application activity was not disabled or comprehensively monitored.

The timing order was exhaustive/A/BCJ2, then BCJ2/A/exhaustive, then exhaustive/A/BCJ2. Each trio had its own 900-second overall watchdog and 300-second phase limits. Global repetition IDs, execution positions and one comparison ID distinguish this cohort from the pilot.

`step3-blender-pilot-results.md` and `step3-blender-timing-results.md` contain the validated planned tables. The adjacent JSONL, explicit plans, budget records and `step3-evidence-provenance.json` preserve settings, commands, identities, original evidence hashes and outcomes.

## Decision boundary

Candidate A is a promising opt-in method on this development file. The default remains exhaustive. Its extra EXE32-rival removal may lose on other files; no general x86 claim or default change follows from this result. Raw-trial skipping is a separate, unimplemented hypothesis. Broader component controls, decoder-memory work, freeze and independent evaluation remain outstanding. Holdouts and reserves remain untouched. No further experiment is queued.
