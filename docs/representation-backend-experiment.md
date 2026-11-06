# Best-Compression Experiment Plan

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 1, 2026.

**Executed November 3, 2026:** the maintainer authorized task 4 using the frozen baseline and these saved limits. The three probe cells round-tripped correctly and duplicate B was byte-identical. The pilot stopped after 237.250 seconds at its preregistered feasibility gate: scaling the complete probe cells to 2 MiB gave 628.744–633.744 seconds, above the 300-second cell limit. All 35 main cells remain unstarted; hypothesis gates are not evaluated. See the [execution report](../reports/backend-experiment.md). The core reviewer and the benchmark reviewer's original design and limits below remain unchanged; this authorization superseded its earlier plan-only status.

Scheduling update, November 3, 2026: this pilot is task 4 in the [AHPS task order](next-steps.md), after reference capture, POLY2 deduplication checks, and profiling, and before standalone-SPLIT candidate changes. It uses the frozen reference representation. This ordering leaves the protocol, controls, budgets, and execution prerequisites below unchanged.

Maintainer decision after the probe: skip further backend execution because of its projected runtime and proceed to task 5. The proposed longer batch/global budgets were not accepted. Preserve the completed probe evidence; no revised-budget run is scheduled.

This experiment is a small 2-by-2 test: do compression gains come from AHPS's data transforms, from a stronger back end, or from both together? It compares RAW and transformed data under LZMA2 and under one pinned context-mixing compressor, on five 2 MiB development slices.

This tests a hypothesis, not a conclusion. The core reviewer proposed building a context-mixing compressor from scratch, and the benchmark reviewer proposed comparing strong existing back ends before inventing another. We agreed to test first with an existing tool, write no new compressor, and let a rule fixed in advance decide what comes next. The result is a recommendation to the maintainer, not an automatic next step.

## Goal and budget

The goal is the smallest complete archive that restores the original bytes exactly, with size counted including every program table, wrapper and transmitted parameter. Speed and memory are recorded as costs, because a mature context mixer may be far slower than LZMA in both directions.

| Limit | Value |
| --- | --- |
| Each batch | 15 minutes, one batch per parent file, stopping before it overruns |
| Each cell | 300 seconds for one complete encode and decode, including framing and inverse transforms |
| Planner preparation | At most 60 seconds per input, including creating and verifying both containers; charged to its batch and the global ceiling |
| Whole experiment | 45 minutes across probes, planning, controls and the determinism rerun |
| Repetitions | One measured matrix pass, plus a predetermined probe-cell duplicate to check repeatability on the same build |
| Memory | Proposed hard limit of 8 GiB aggregate committed memory for the active worker process tree; peak resident memory recorded separately |

Both reviewers recommend a 45-minute total ceiling, subject to the maintainer's decision. A parent batch has a 15-minute ceiling, not an entitlement to 15 minutes on top of the global budget. Probes, preparation, controls, failed jobs and the repeatability check all consume the total.

One worker runs at a time. The earliest cell, batch or global deadline applies, and the worker tree is terminated when it expires. Memory is enforced with a process-tree or job-object limit, because observed peak memory alone isn't a hard limit. If the limits can't be enforced, the run stops before the matrix. Changing a budget needs a new maintainer decision and a recorded protocol revision. The 45-minute timer starts with the timed probe. Acquiring the tool, reviewing the harness and maintainer review are prerequisites, recorded separately, and aren't compressor runtime.

## Hypotheses

Each hypothesis has a result fixed in advance, so the answer can't be chosen after seeing the numbers. All sizes are complete archive bytes, per input, across the four non-control slices.

| Hypothesis | Supported if | What we then recommend |
| --- | --- | --- |
| A stronger back end matters | The best context-mixing cell is at least 5% smaller than the best LZMA cell on at least 3 of 4 | A separately approved whole-file follow-up. This is not a product pass |
| Existing transforms carry over | At least 3 of 4 inputs each have changed residual bytes and satisfy `1 - D/B >= 0.01` under PAQ8PX | Further testing of the transforms as a front end |
| Existing transforms don't carry over | Under PAQ8PX, RAW is at least 1% smaller than transformed: `1 - B/D >= 0.01` on at least 3 of 4 | Redesigning modelling or representation. It doesn't show all transforms are useless |

A result can land outside these rows. Mixed or inactive cases are reported as inconclusive. Missing the 5% rule ends this pilot, but doesn't reject context mixing in general.

The `webster` slice is the negative control. If `min(B, D) / min(A, C) - 1` exceeds 1% there, the report says routing or a RAW fallback needs investigation, and makes no universal replacement claim. That doesn't veto a possible win on other data. Losses in either PAQ8PX cell and in the three naked controls are also reported. Any correctness, timeout or resource failure stays visible and blocks a positive result.

## Approach

The factorial comparison varies representation and back end. Within each row, both back ends receive exactly the same frozen container bytes. Within each back-end column, RAW and transformed use the same codec settings. The result isolates these implementation choices under this protocol, and doesn't establish a universal ranking of compression families.

| Input representation | LZMA2, preset 9 extreme | PAQ8PX v217, `-8 -forcebinary` |
| --- | --- | --- |
| RAW AHPS container | Cell A | Cell B |
| Transformed AHPS container, frozen plan | Cell C | Cell D |

**Controls.** Three extra cells compress the naked original slice: E, XZ with the same preset 9 extreme settings; F, PAQ8PX `-8 -forcebinary`; and G, PAQ8PX `-8` with automatic detection. E and F measure the effect of AHPS framing with the back-end configuration held constant. G is a whole-codec reference, not an isolated context-mixing result. That makes seven cells per parent and 35 main cells in total. All native codec headers count in the controls.

**Why `-forcebinary`.** PAQ8PX has its own detection and transforms, which could explain a win that AHPS's rows would otherwise claim. Forcing its generic binary model keeps the comparison about the back end. No other options are used: no large-memory, tuning or pretrained-weight flags.

**Framing.** Both primary rows use one experimental wrapper that names its decoder, and all its bytes are counted, including real program-table costs. They aren't literally naked bytes, which is why the controls exist. The experiment never writes a normal AHPS archive with a swapped back end.

Before execution, a reviewed harness specification freezes the wrapper layout, identifier, version, codec ID and settings, lengths, integrity fields and inverse-dispatch rules. The wrapper is physically stored and every byte is charged, because a hypothetical size adjustment isn't enough. The matrix wraps both codecs the same way, while naked controls keep their native formats. Each stored artifact decodes through its declared path to the original bytes, using clean output paths so decoding can't merely compare against an existing original. Normal AHPS v5 and ABI 206 behaviour stays unchanged.

**Plans.** The current planner chooses each slice's plan at preset 9 extreme, and that plan is frozen before either PAQ8PX run. These plans were chosen for LZMA, so the test asks whether existing choices transfer, not what the best plan for PAQ8PX would be. Per-backend search is a separate follow-up.

**Plan provenance.** The planner uses explicit `search=ahps.FAST_CHUNKS` and `lzma_preset=9`, resolved to 9 plus EXTREME, with the normal RAW fallback kept. The record includes all resolved search and LZMA filter parameters, dictionary size, XZ checksum, Python and liblzma versions, AHPS commit, and native-library hash and ABI. The selected v5 container is extracted from the current compressor's XZ output, not cropped from a whole-file residual. The RAW container has one zero-step segment, and both containers are hashed before either back end runs.

A RAW-winning selection may make the two rows identical, and that is reported as it is. Changed bytes are counted in the residual against the original, excluding table and header bytes. All 2 MiB slices are below AHPS's 16 MiB chunk threshold, so this pilot can't evaluate per-chunk selection.

**Where we differ.** We share the first step and differ on what it implies.

| Question | Core reviewer | Benchmark reviewer |
| --- | --- | --- |
| Starting bet | Context mixing is likely the right architecture | A reasonable hypothesis, not a presumed winner |
| If it wins | Build specialized models, including an instruction-aware x86 model | Compare strong back ends and format-aware streams further |
| Generators | One more predictor in the mixer, added late | Not free: detection still costs time and state, so they stay separate |
| Determinism | Keep any neural part integer-only | Required, but integer-only is one route, not a rule |

**Out of scope.** Writing a new context mixer, generators, neural compressors, `cmix`, new transforms, per-backend plan search, production format or default changes, and all sealed inputs and reserves. `cmix` is a possible later reference, budgeted separately. It is not treated as an upper bound.

## Phases

The pilot runs in six stages, and each can stop it.

1. **Prepare and freeze.** After the maintainer authorizes execution, acquire the pinned back end, and record its exact executable identity, the harness, wrapper specification, commands, resource enforcement and development manifest. The benchmark reviewer implements the experimental harness, the core reviewer reviews it and the container extraction, and both freeze it before timing. The sole runner is announced in root `xchange.txt`.
2. **Probe feasibility.** Use the first 256 KiB of the predetermined Blender slice, at parent offset 15 MiB. Plan this short input independently and run primary cells B and D. Repeat probe B once and compare complete archive hashes and restored bytes. All three runs and preparation share a single 15-minute probe batch and count against the global ceiling. Timings are used only to estimate whether the 2 MiB screen fits. If it clearly can't, stop with a feasibility report, and don't shrink or move samples. Linear estimates are diagnostic, not a promise.
3. **Freeze main inputs.** In table order, verify each development parent against the existing manifest, extract its fixed slice, record offset, length and hashes, create both containers under the preparation cap, and freeze their plans and hashes. Stages 3 and 4 run together for each parent, so its 60-second preparation happens immediately before its seven cells, inside the same 15-minute batch. Parents aren't prepared ahead while their batch timers run.
4. **Run the matrix.** The benchmark reviewer runs parents in the listed order and cells A through G in that order, once, in separate clean processes. For each parent, preparation and all seven cells share its 15-minute batch. No other benchmark overlaps, and methods don't change after a partial result. An interrupted or unstarted cell is recorded, not replaced, and gets no fresh deadline.
5. **Audit and report.** The core reviewer checks commands, identities, byte counts, round trips, deadlines and all failures against the frozen protocol. The benchmark reviewer reports every planned cell and the predefined comparisons. One pass supports exploratory size findings and diagnostic cost measurements only.
6. **Maintainer decision.** Stop. The report recommends a whole-file study, a smaller separately preregistered feasibility study, representation work, or no follow-up. No follow-up is automatic.

## Evaluation rules

Every input is fixed before anything runs, and no slice moves after results are seen. Only development data is used.

| Parent file | Role | Slice |
| --- | --- | --- |
| `blender` | Real x86 program | 2 MiB at offset 15 MiB |
| `node` | Second x86 program | 2 MiB at offset 15 MiB |
| `x-ray` | Structured data | 2 MiB at offset 0 |
| `mr` | Structured data | 2 MiB at offset 0 |
| `webster` | Negative control, historically RAW | 2 MiB at offset 15 MiB |

The rule is mechanical: offset 15 MiB when the parent is at least 17 MiB, otherwise offset 0. These slices can't be called representative, and a slice from an x86 file may not contain active code, so nothing is assumed in advance. Each record includes the actual plan, changed-byte count, active-opcode coverage, and any identity or RAW case. The `webster` slice isn't guaranteed to be RAW either. Low transform coverage makes the transform question inconclusive, and a follow-up would need its own preregistration.

**Each record must include:** exact tool versions and hashes, the method and file identity, complete archive bytes and hash, a verified byte-exact round trip, and any failure or timeout. If PAQ8PX v217 can't be obtained and verified, the experiment stops with no substitute.

**Calculations.** With A through D as total stored archive bytes, back-end gain is `1 - min(B,D)/min(A,C)`. Transform gain is `1 - C/A` under LZMA and `1 - D/B` under PAQ8PX. Positive means smaller. The report also gives the interaction `ln(D/B) - ln(C/A)`, where a negative value means the proportional transform gain was larger under PAQ8PX. These are descriptive development comparisons, not significance tests. Each input is reported separately, and averages can't hide a regression or replace the per-input counts.

**Costs and completeness.** The record covers container preparation and search time, back-end encoding time, decoding and inverse time, total round-trip time, archive bytes, compression and decompression peak resident memory, the enforced memory limit, exit status and exact failure reason. Time with a prepared container is kept separate from the full preparation-plus-codec cost. An absent parent or cell stays in the denominator and isn't a success. No positive gate is declared unless all planned main cells finished correctly within limits. A slow probe or exhausted deadline is a feasibility failure under these limits, not evidence that context mixing can't improve compression.

**Reporting.** Slice results can't support whole-file claims. Times are diagnostic only, so no stable timing or memory ratio is reported. Executable and structured-data findings stay separate. All cells are reported, including losses and failures. No sealed Silesia file, application holdout or reserve is read.

## Source pin

PAQ8PX v217 was pinned from upstream metadata. Its distribution was acquired on November 3, 2026 and matched the digest below. The extracted sole Windows x64 executable hashes to `4e530a62a69f7f5928f494d85492898ad21b43e3a2e9c2953a3307d5c5bb5ed4`; it uses automatic runtime SIMD selection with no override.

- PAQ8PX [release v217](https://github.com/hxim/paq8px/releases/tag/v217), source commit [`c84f576fc2c522194cd320743708652a154daf6b`](https://github.com/hxim/paq8px/commit/c84f576fc2c522194cd320743708652a154daf6b).
- Official asset `paq8px_v217_windows_x64.7z`, published SHA-256 `f5be98bff57db88e0bf1dc4ab309a87ab7c48fa169cc21d2fb89dd8180162f1c`, from the [release asset listing](https://github.com/hxim/paq8px/releases/expanded_assets/v217). This hashes the distribution archive, not the extracted executable. It is verified on acquisition, and the chosen executable's name, CPU variant and SHA-256 are pinned before execution.
- The [tagged README](https://raw.githubusercontent.com/hxim/paq8px/v217/README.md) documents the CLI, forced-binary mode and the GPL-2.0-or-later licence. Upstream notices are kept, and PAQ8PX runs as a separate executable. This plan proposes no source-code reuse. Level 8 with forced-binary modelling is a bounded test configuration, not a claim about the best PAQ8PX can do.
- Repository context: [format contract](format.md), [search defaults](search-methods.md), and [evaluation protocol](../reports/evaluation-protocol.md). This exploratory pilot does not replace or satisfy the sealed final-evaluation acceptance rules.

## Risks and open decisions

The largest risk is that five 2 MiB slices are too small or unrepresentative to justify anything beyond a follow-up.

| Risk | Effect | Handling |
| --- | --- | --- |
| Slices contain little active code | The transform question can't be answered | Record coverage and report it as inconclusive; don't move the slice |
| Plans were chosen for LZMA | Transforms may look worse under PAQ8PX than they could be | Report it as a transfer test; per-backend search is separate |
| PAQ8PX adds its own modelling | A back-end win could be misattributed | `-forcebinary`, with the automatic control reported separately |
| Short inputs favour some models differently | Results may not scale to whole files | No whole-file claims from slices |
| Speed is unknown | The matrix may not fit its budget | The 256 KiB probe comes first. With four PAQ8PX cells per parent, 35 cells may not fit in 45 minutes, in which case the pilot stops with a feasibility report |
| Findings are tuned to these inputs | Overconfident conclusions | Development data only; sealed inputs stay sealed |

**Decisions for the maintainer.**

| Decision | Why it is needed | Recommendation |
| --- | --- | --- |
| Authorize preparing and running this bounded protocol, including acquiring the pinned PAQ8PX package | This request created a plan, and no execution is authorized by the document | Review the scope, seven-cell matrix and limits together; pin the extracted executable and harness before timing |
| Confirm the proposed limits | Complete cell 300 seconds, preparation 60 seconds per input, batch 15 minutes, total 45 minutes, worker tree 8 GiB memory | Keep these limits for the pilot; a resource failure is a reported outcome |
| Any whole-file follow-up | It could take hours at context-mixing speed | Decide after the report |

The original document requested authorization rather than granting it. On November 3, 2026 the maintainer explicitly authorized this bounded execution. Executable identity, harness review and enforcement validation were recorded before timing. The pilot ended at its feasibility stop; no production change or follow-up was performed.
