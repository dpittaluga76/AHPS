# Bounded Blender component checks

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

All six new encode/decode pairs passed exact SHA-256 round-trip checks. The
first five finished in **331.2 seconds**, within one 900-second budget. The
separately approved per-chunk removal finished in **153.2 seconds**, within its
600-second encoding cap and 630-second overall watchdog. Each configuration
ran once; there were no failures or retries. Only the already-developed
`blender.exe` input was used. No other executable or sealed corpus file was opened.
These are size and correctness observations, with no timing or memory claim.

## End-to-end EXE_SPLIT removal

| Configuration | Complete AHPS archive bytes | Increase over default |
| --- | ---: | ---: |
| Historical default, reproduced by the core reviewer with the reviewed core | 26,380,608 | — |
| `exe_split=false`, all other features enabled | 26,466,520 | 85,912 / 0.326% |

The removal emitted no EXE_SPLIT. Its planner used EXE32 in six reported
segments covering 78,741,504 bytes. This measures the incremental benefit of
having EXE_SPLIT available alongside the EXE32 fallback; it does not measure
the value of all executable preprocessing.

Without EXE_SPLIT the archive remains **825,515 bytes (3.02%) smaller** than
the historical 27,292,035-byte BCJ2 archive. The 85,912-byte EXE_SPLIT increment
equals **9.43%** of the default's 911,427-byte lead over that BCJ2 result on
this file. This is a size decomposition, not a claim about its runtime cost.

This is an explicitly **cross-commit size comparison**, kept outside the
generated corpus matrix. The historical default is from
`1d436f33090276b64a841709393833f4e15bef27`, and all three historical default
archives have SHA-256
`975c59635d6fc1812502364f13c1e2ea4d6b5ba188f5692c6ffe359a887887cd`.
The core reviewer reported in root `xchange.txt` that reviewed core
`5ac59a308a47dcad80bd79162ee5dd0c30432193` reproduced the same archive bytes,
hash and round trip. That peer check supplies the connection; the default was
not rerun in this bounded batch. The new removal ran at
`74bde36f133c387d0c75b19b3d36b83ea49b6f96`, containing that same core and DLL.
Historical timings and memory figures are not paired performance controls.

## Per-chunk selection removal

| Configuration | Complete AHPS archive bytes | Increase over default |
| --- | ---: | ---: |
| Historical default, reproduced by the core reviewer with the reviewed core | 26,380,608 | — |
| `per_chunk=false`, all other features enabled | 27,770,972 | 1,390,364 / 5.270% |

The whole-file search selected one EXE_SPLIT segment over all 113,041,880 source
bytes. Its archive is **478,937 bytes (1.755%) larger than historical BCJ2**.
This is an informative removal: the default's mixed per-chunk selection has a
substantial size benefit on Blender and changes the outcome against BCJ2.
The Silesia Samba removal had not established this because both its winners
were RAW. No runtime or memory benefit is inferred from these single runs.

This additional row was produced at
`39a4acc4e2659780ee7858e5347dbdd953d48304`, whose changes since `74bde36` were
reports only. The supervisor verified every implementation/runtime artifact
hash against the earlier removal before execution; the resulting row records
the same artifact identities and source hash. It uses the same explicitly
cross-commit historical-default comparison described above. The feature
removals change plan selection, so their differences must not be added.

## Separate predicate experiment

Each transform processed the entire file at base offset zero, followed by
ordinary XZ preset 9 extreme. **These are residual-XZ sizes, not complete AHPS
archives**, and do not represent the planner's selected segments.

| Transform | Production ABI 206 | No-ModRM ABI 9206 | Increase when removing predicate |
| --- | ---: | ---: | ---: |
| EXE32, unchanged control | 27,939,148 | 27,939,148 | 0 bytes |
| EXE_SPLIT | 27,771,440 | 27,886,324 | 114,884 bytes / 0.414% |

The EXE32 control also has identical residual-XZ SHA-256 under both libraries:
`52231bf7a81f5c5cc82a01228c9061d73f6453fee34fd303e29bea21b7f59460`.
Every case used its matching inverse. The experimental library remained
isolated and no experimental AHPS archive was emitted.

The current predicate helps this whole-file EXE_SPLIT experiment. Its effect
and the end-to-end removal result are **not additive**. Neither is comparable
to earlier code-section-only figures. Both favor retaining the current
behavior provisionally; they do not establish general x86 performance, settle
the search-cost tradeoff, or complete the broader component/interaction gate.

Within this separate residual-XZ experiment, production EXE_SPLIT is 167,708
bytes smaller than EXE32. The predicate's 114,884-byte benefit accounts for
**68.50% (about 69%)** of that difference. This percentage concerns whole-file
transform outputs, not the complete AHPS archive's lead over BCJ2.

## Evidence and reproduction

- `blender-component-20261101.jsonl`: the one complete-archive removal.
- `blender-predicate-20261101.jsonl`: all four transform/variant cases.
- `blender-checks-20261101.budget.json`: exact commands, 300-second phase caps,
  shared 900-second budget, completion status and elapsed batch time.
- `blender-checks-20261101.provenance.json`: externally recorded commit context
  for the predicate schema, historical reference, and original evidence hashes.
- `blender-no-per-chunk-20261101.jsonl` and its `.budget.json`: the additional
  removal, exact command, verified unchanged artifact identities and limits.

All rows use the same 113,041,880-byte source, SHA-256
`9d5df7a9ac2a7c5085aa0669700021bfa5c5e92de167a2510b0d0942c34deab0`.
The component run used native ABI 206, preset 9 extreme and block size 4096.
The predicate rows pin source, production/experimental DLLs, compiler/build,
Python and liblzma identities; their schema does not itself contain a Git commit,
so the companion provenance records that batch context explicitly.

There are no queued runs. Final candidates, reserves, and `ooffice`, `osdb`,
`xml` remain sealed.
