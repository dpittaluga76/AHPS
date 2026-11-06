# AHPS evaluation protocol

> Historical development record. Measurements and execution status refer to the inputs and builds described below. Local corpus files, archives, and raw logs may be absent from the public checkout. See the [documentation index](../docs/index.md).

**DRAFT — pending maintainer-reviewed freeze. No final evaluation is authorized by this document.**
Prepared 2026-10-31 from the agreed working targets and `corpus_manifest.json` metadata. No sealed file contents were inspected to prepare it. The eligibility rules below are proposals to settle before unsealing.

## Question and working acceptance targets

Does one frozen AHPS candidate improve compression of independent native x86/x64 applications enough to justify its encoding time and decoding memory against 7-Zip BCJ2?
Keep the working targets agreed before development evaluation; do not adjust them to observed results:

- At least 8 of 10 eligible independent final files must produce archives at least 2% smaller than BCJ2.
- No final file may produce an archive more than 1% larger than BCJ2.
- Separately, the geometric mean of per-file encoding-time ratios must be at most 3.0.
- Separately, the geometric mean of per-file decoding peak-working-set ratios must be at most 2.0.
- Research success requires the two size conditions; product success additionally requires both resource conditions. Both claims require complete, verified measurements on all 10 files. Development results cannot satisfy either independent-evaluation target.

For file `i`, let `A_i` and `B_i` be complete AHPS and BCJ2 archive sizes in bytes, including their containers; `T_Ai`, `T_Bi` the median encoding times; and `M_Ai`, `M_Bi` the median decoding peaks across three runs.
Define `R_i = A_i / B_i`, `saving_i = 100 * (1 - R_i)`, `t_i = T_Ai / T_Bi`, and `m_i = M_Ai / M_Bi`.
Thus `count(R_i <= 0.98) >= 8`, `max(R_i) <= 1.01`, `exp(sum(log(t_i))/10) <= 3`, and `exp(sum(log(m_i))/10) <= 2`.
Publish every repetition's exact byte count and archive hash. Require equal sizes across repetitions of the same frozen codec/input configuration. Report variation as an unresolved reproducibility failure, not a choice of which repetition to score.
Do not replace these conditions with a ratio of corpus totals, an average of all timings, or a combined time/memory score.

## Data separation

Development IDs: `blender`, `dickens`, `mozilla`, `mr`, `nci`, `reymont`, `samba`, `sao`, `webster`, `x-ray`, `7zip-cli`, `git-cli`, `node`, `vlc`, `chrome`.
Blender, Mozilla and Samba influenced the design. All development results remain tuning evidence, regardless of how many repetitions or chunks are measured.
The proposed final x86 IDs, in manifest order, are `audacity`, `gimp`, `github-cli`, `firefox`, `notepad-plus-plus`, `obs`, `winrar`, `wiztree`, `hwinfo`, `waveform`.
Fixed reserve order: `ffmpeg`, `obsidian`, `fl-studio`, `codex-cli`.
Silesia `ooffice`, `osdb` and `xml` remain a separate sealed check; they do not fill vacancies in the 10-file x86 experiment or establish its acceptance target.
Until the maintainer approves the candidate and protocol freeze, do not open, hash, classify, copy or benchmark any sealed candidate or reserve. Names, paths and inventoried sizes do not establish architecture or packing status.

## Proposed eligibility and reserve rules

After freeze, snapshot and hash candidates; apply the same frozen eligibility procedure to every candidate before running any compressor. Do not execute candidate binaries.

- Require a readable, complete installed application image with valid DOS/PE headers, consistent section bounds, and machine `0x014c` or `0x8664` with the corresponding PE32/PE32+ optional header.
- Proposed stronger code-content screen: allow application executables and DLLs, require at least 2 MiB total file size and 1 MiB of file-backed executable sections, and exclude CLR-only images using the CLR metadata flags. For an executable, require an entry point in file-backed executable code; a DLL may legitimately have a zero entry point. These thresholds remain an maintainer decision before freeze.
- Exclude positively identified installers, self-extracting packages and packed/protected loaders using a tool version and signature database pinned at freeze. Preserve the specific identification evidence; no ad hoc signature additions after inspection.
- An entropy score or absence of a known packer signature does not prove an image is unpacked. Record packing status as identified, unrecognized or ambiguous. Ambiguous identifications require the predeclared adjudication rule, without compression results.
- Proposed primary population: native installed PE images passing this screening, described as packing-screened rather than proven unpacked. If the maintainer requires positively verified unpacked builds, pin the evidence requirement and ambiguous-case disposition before unsealing.
- Count at most one file per application/product lineage; exclude exact duplicate hashes, alternate builds of an already selected product, and files previously used for AHPS tuning/census. Record provenance and shared-runtime relationships; do not claim statistical independence from file count alone.

An exclusion requires a recorded, predeclared eligibility reason. Fill each vacancy with the next eligible reserve, in fixed order, before compression begins. Record rejected reserves too.
Open freeze decision: retain an eligible main executable and otherwise try the product's largest eligible PE, or always select its largest eligible PE. `holdout-path-options.json` records the main file plus up to five alternatives ranked using filenames and filesystem sizes only. No replacements have been made. Pin the selected rule and fallback list before opening contents. Large DLLs can be shared runtimes rather than application-specific code; report those relationships and apply the same rule to every product.
Changed installed-file sizes are recorded; the exact approved snapshot, hash and version define the input. A changed size alone is not a performance-based exclusion.
If fewer than 10 eligible distinct application files remain after the reserves, publish the reduced sample and exclusions without claiming the 8-of-10 target. Do not add convenient new candidates after seeing results.
Crashes, timeouts, poor ratios and high memory use are outcomes, never eligibility exclusions or reasons to substitute a reserve.

## Measurement and frozen settings

- Use the shared root benchmark lock. Run one file/codec/phase at a time, with no other benchmark or compression jobs; keep machine, power policy and storage constant and record them.
- Run three fresh encoding processes and three separate fresh decoding processes per file and codec. Record every repetition; report median times and median peaks, plus the range. Fix codec/file order before the first run.
- Primary time is process launch through exit, including imports and I/O. Peak memory is the kernel's peak working set for the actual measured process, separately for encoding and decoding. Do not mix inner operation times with process timings.
- Record cache policy: no cache flush, decode follows encode. Hash checks occur outside timed worker operations. Verify every decoded output against the immutable source SHA-256.
- Primary rival: 7-Zip BCJ2 with `-t7z -mx=9 -mf=BCJ2 -md=64m -mmt=1 -ms=off`; verify BCJ2 on the actual file entry and save its technical listing. Record the effective coder/dictionary details.
- AHPS: `-p 9`, block size 4096, native ABI confirmed, no silent Python fallback. XZ and XZ+BCJ at preset 9 extreme are secondary baselines, not replacements for the BCJ2 acceptance comparison.
- Freeze the exact AHPS, harness, tests and native build hashes/commit; Python, liblzma and 7-Zip artifacts/versions; commands, presets, dictionaries, thread counts, source snapshots and manifest. Record exact archive byte counts and hashes; pin final archive retention for independent verification.
- Any source, implementation or manifest change during a run invalidates that run and is recorded. Freeze the benchmark artifacts for the whole evaluation, not just individual repetitions.

Historical concurrent timings and peaks are context only. Do not combine them with new serial results or use them to pass a target.

## Limits, failures and final freeze

For development, explicitly pass `--timeout 900`: 900 seconds per encoding or decoding phase. The harness currently defaults to 1800 seconds, so its default does not implement this development policy.
There is no claimed OS/process memory hard cap. Peak working set is observed; AHPS decoder limits are separate controls, not a process-memory budget.
AHPS decoder defaults: `max_dict_bytes = 256 MiB`, `max_container_bytes = 4 GiB + 1 MiB`, `max_output_bytes = 4 GiB`. Record limit errors distinctly from corruption, crashes and round-trip mismatches.
Before unsealing, the maintainer must pin final phase timeouts, AHPS decode limits, baseline settings, machine conditions, eligibility tools/rules, reserve order, artifact hashes and the final manifest. Development's 900-second limit is not automatically the final limit.
The current harness accepts development inputs only. A separate reviewed final-evaluation mode must enforce the freeze and retain the final archives before any sealed run can start.
Keep failures and every attempted file in the report and denominator. Do not calculate acceptance from successful files alone. A failed repetition, missing metric, failed round trip or baseline failure prevents a passing claim until resolved under the frozen rerun policy; do not silently replace it with a successful attempt.
The freeze must specify which independently documented infrastructure faults permit a rerun. Preserve original failed records and the reason; codec errors or timeouts are not infrastructure exemptions.
Select one final candidate using development data only. After unsealing, perform the declared final experiment once, with its three repetitions; no heuristic, threshold, predicate, chunking or parameter retuning against those results.
If a defect requires a candidate change after unsealing, report the interrupted evaluation and use a new independently selected sealed set for a new confirmatory claim.
Publish all per-file sizes, savings, encode/decode times and peaks, failures, exclusions, reserve substitutions, hashes and settings, alongside the four separate acceptance outcomes. Clearly label development/preliminary evidence and the final independent-file experiment.
