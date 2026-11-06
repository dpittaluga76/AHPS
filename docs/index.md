# Documentation and research index

Start with the [repository README](../README.md) for installation and normal
CLI use. The current implementation includes ordinary binary transform search,
optional CSV column grouping, optional safetensors representations for inputs
up to 32 MiB, terminal progress, and `bench --ahps-only`.

## Current behavior

| Document | Contents |
| --- | --- |
| [Usage and API](usage.md) | CLI commands, Python API, input limits, and synthetic benchmark setup |
| [Native builds](building.md) | Rebuilding the optional accelerators and checking their ABIs |
| [Source release preparation](releasing.md) | Reviewed public inventory, clean export, and history separation |
| [Public source validation](../reports/public-source-validation.md) | Local source/native/package checks and pending hosted CI |
| [Archive format](format.md) | v3/v4 compatibility, ordinary v5, CSV v6, tensor v7, native ABIs, and decoder limits |
| [Search methods](search-methods.md) | Preset-dependent automatic search and explicit benchmark settings |
| [Implementation status](next-steps.md) | Current features, remaining work, and completed task history |
| [Research usage](../research/README.md) | Experimental codecs and their separate formats and dependencies |

## Development evidence and availability

Reports describe particular inputs, implementations, settings, and dates. Their
test counts and execution statuses belong to those recorded runs. Timing from
different cohorts should be interpreted with the limitations in each report.
The final independent evaluation remains unfinished.

Local corpus files, model weights, CSV datasets, executables, experiment
archives, private source PDFs, and some raw logs are not supplied with every
public checkout. A local artifact path identifies the original evidence record.
It does not imply that the file can be downloaded from this repository.
Preserved hashes and measurements help identify the recorded inputs and outputs.
Reproduction requires compatible source data, tools, and the stated settings.

Historical protocols include local approval gates and runner assignments that
applied to their original studies. They document how those measurements were
controlled. They do not schedule new work for contributors.

## Recent production features

| Report | Scope |
| --- | --- |
| [CSV compression](../reports/csv-compression.md) | Exact column grouping, complete-archive sizes, and parallel trial measurements on one CSV |
| [Safetensors-aware AHPS](../reports/safetensors-aware.md) | F32 precision elision and FP8 rANS coding on three 15 MiB slices |
| [Earlier model-weight comparison](../reports/weight-chunks.md) | Generic codec and transform controls preceding the integrated tensor feature |
| [Terminal progress](../reports/terminal-progress.md) | UI behavior, archive equality, measured overhead, and environment-limited tests |

## Binary transform and decoder studies

| Study | Protocol or design | Outcome |
| --- | --- | --- |
| POLY2 candidate deduplication | [Recorded task order](next-steps.md) | [Validation](../reports/poly2-dedup-validation.md) |
| Encoder and decoder profiling | [Recorded task order](next-steps.md) | [Profile](../reports/profiling.md) |
| Representation and PAQ backend | [Protocol](representation-backend-experiment.md) | [Feasibility stop](../reports/backend-experiment.md) |
| Standalone byte splitting | [Protocol](standalone-split-experiment.md) | [MR study](../reports/standalone-split.md) |
| Incremental decoder assembly | [Protocol](decoder-memory-experiment.md) | [Rejected candidate](../reports/decoder-memory.md), [full report](../reports/decoder-memory-20261103.md), [result table](../reports/decoder-memory-results.md) |

## Relational and independent-codec research

| Study | Protocol | Outcome and audit |
| --- | --- | --- |
| Known integer records | [Initial protocol](relational-compression.md), [larger table follow-up](relational-table-followup.md) | [Results](../reports/relational-compression.md), [audit](../reports/relational-audit-20261103.md) |
| Complete-file relational wrapper | [Protocol](relational-corpus-experiment.md) | [Results](../reports/relational-corpus.md), [audit](../reports/relational-corpus-audit-20261103.md) |
| Matched table transforms | [Protocol](same-boundary-table-experiment.md) | [Results](../reports/same-boundary-tables.md), [audit](../reports/same-boundary-tables-audit-20261103.md) |
| Selective word-transform hybrid | [Protocol](selective-word-hybrid-experiment.md) | [Results](../reports/relational-hybrid.md), [audit](../reports/relational-hybrid-audit-20261103.md) |
| Independent LZ77/arithmetic backend | [Protocol](independent-backend-experiment.md) | [Results](../reports/independent-backend.md), [audit](../reports/independent-backend-audit-20261103.md) |

## Original x86 and search evidence

| Topic | Records |
| --- | --- |
| Historical technical review | [Retrospective](project-retrospective.md) |
| Decoder dependability | [Dependability report](../reports/dependability.md) |
| Corpus development matrix | [Protocol](../reports/development-matrix.md), [overview](../reports/corpus-results-overview.md), [results](../reports/corpus-results.md) |
| Blender baseline and components | [Baseline](../reports/baseline-blender-step2.md), [components](../reports/blender-components-overview.md), [Step 2 handoff](../reports/step2-handoff.md) |
| Search-cost experiment | [Protocol](../reports/step3-search-protocol.md), [overview](../reports/step3-search-results-overview.md), [pilot](../reports/step3-blender-pilot-results.md), [timing table](../reports/step3-blender-timing-results.md) |
| Search default decision | [Development overview](../reports/step3-development-a-overview.md), [development results](../reports/step3-development-a-results.md), [Blender results](../reports/step3-blender-default-results.md), [default validation](../reports/preset9-default-validation.md), [Step 3 handoff](../reports/step3-handoff.md) |
| Final evaluation design | [Draft protocol](../reports/evaluation-protocol.md) |
