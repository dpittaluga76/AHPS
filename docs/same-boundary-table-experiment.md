# Same-boundary exception-table comparison

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

November 3, 2026. Owner-authorized research on `codex/relational-compression`,
based on checkpoint `de5f78c`. The maintainer is using the PC: controllers, tests and
workers run at verified below-normal priority; all timing is diagnostic. This
protocol is written before extracting or measuring the new table cohort.

## Question and fixed inputs

Does the earlier word-delta gain require different arithmetic, or can existing
AHPS byte-delta/SPLIT programs match it on the same known record boundaries?
AHPS already supports stride 12. Do not add an opcode, change either codec,
alter the production planner, or build the whole-file hybrid in this experiment.

Fixed parent order: Blender as the previously used reference, then the exact
`corpus_development` files `7zip-cli.exe`, `chrome.exe`, `git-cli.exe`, `node.exe`,
`vlc.exe`. Check each complete parent against its development-manifest SHA-256
and size, and bracket extraction with those hashes. Use the existing validated
PE32+ x64 exception-directory parser. Preserve the complete declared table,
its file offset/RVA/length and parent identity, with a maximum of 8 MiB matching
the unchanged AHRL codec limit. Tables contain three little-endian uint32 fields
per 12-byte record. No padding, subwindows, replacement files or other regions.
Absent, malformed, oversized or unavailable tables remain recorded outcomes.
No sealed files/reserves, downloads or online research are used.

Every method receives identical bytes from index zero through the end of that
table. Fixed methods use one segment/region with no internal resets. Automatic
AHPS receives those same bytes but may choose internal boundaries; record them.
This is a table comparison, not a whole-file result. Parent offsets are provenance
and are not substituted for the standalone input's zero origin.

## Seven fixed methods, in order

1. `xz`: bare XZ of the original table.
2. `ahps_raw`: one AHPS v5 RAW segment.
3. `ahps_split12`: one AHPS segment with SPLIT(12).
4. `ahps_delta1_split12`: one segment with DELTA1(12, d=0), then SPLIT(12).
5. `ahps_delta2_split12`: one segment with DELTA2(12), then SPLIT(12).
6. `ahrl_word_delta`: unchanged AHRL `delta`, width=4, fields=3, whole table.
7. `ahps_auto`: frozen production AHPS, native ABI 206, block=4096, explicit
   preset=6 and `FAST_CHUNKS`, all normal features unchanged.

All compression uses XZ preset 6, CRC64. Fixed AHPS methods use existing
`pack_container` and native transforms and produce ordinary valid AHPS v5
archives. AHRL produces its existing research format. Count complete archive
bytes and verify decoded length and SHA-256. Store the actual serialized programs,
region lengths and model metadata; fixed AHPS methods must have one segment.

## Separate arithmetic diagnostic

For the five fixed representations (AHPS RAW, SPLIT12, D1+SPLIT12,
D2+SPLIT12 and AHRL word delta), extract the actual residual from each validated
archive, then encode it alone with the same XZ settings. Store its bytes/hash
and compressed bytes/hash. Decompress and reverse the recorded fixed transform,
verifying the original table length/hash. These residual-only streams are
diagnostics requiring external transform information, not standalone codecs.

D1(12,0) and uint32 delta both preserve the first record and use identical final
12-byte byte-lane ordering. Their residual comparison isolates subtraction
modulo 256 versus modulo 2^32 under this backend. D2 preserves the first two
records and is an existing rival, not an order-matched arithmetic control.
The residual diagnostic excludes model/header cost. Complete-archive sizes remain
the practical comparison. Never subtract compressed residual size from complete
archive size and call it an additive metadata cost: headers change coding state.

## Execution and interpretation

Use a new immutable artifact directory/report. Pin the baseline, AHRL core,
parser, runner, comparison code, regression tests, protocol and runtime; retain
source snapshots and exact extracted inputs. Verify native DLL identity and ABI.
Controllers and workers must set and read back priority class 0x4000 before
heavy work. Use the shared benchmark lock and one worker per available table,
serially: 120 seconds per table, 600 seconds per batch, 2 GiB aggregate committed
memory, no breakaway, owned-tree cleanup. Do not overlap tests or other benchmarks.
Run one observation per cell; no retuning, retry or silent budget extension.
Unavailable tables retain all seven unstarted archive cells and five diagnostic
slots. All six available inputs would yield 42 complete archive round trips and
30 independently reversed residual diagnostics; report actual denominators.

Record every failure and partial result, original and restored hashes, all
compressed sizes, actual programs/models, API times, priority readbacks and Job
outcomes. Report timing only as diagnostics while the maintainer uses the PC; shared
worker peaks are not per-method memory comparisons.

Report per table: word delta versus fixed D1; word delta versus the best existing
complete-file-format control (bare XZ, all fixed AHPS programs, automatic AHPS);
and the best fixed AHPS program versus automatic AHPS. Present residual-only
rankings separately. If existing programs match or beat word delta, this table
does not establish a need for a new predictor. If fixed AHPS beats automatic
AHPS, it identifies a selection/boundary opportunity, not its precise cause.
Word delta winning both the framed comparison and the D1 residual comparison
supports a word-arithmetic candidate for this table class, not a whole-file win.

Preserve mixed outcomes and do not extrapolate table percentages to executables
or call these reused development files independent final evaluation. This run
does not rerun the earlier timed-out whole-file Node comparison. No production
adoption or format changes follow automatically from any result.
