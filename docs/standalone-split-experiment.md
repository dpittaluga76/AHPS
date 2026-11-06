# Fixed-region standalone SPLIT experiment

> Historical experiment protocol. Execution instructions and approvals below belong to the recorded study. Local inputs and raw artifacts may be absent from the public checkout. See the [documentation index](index.md) for current features and outcomes.

Task 5, November 3, 2026. Authorized by the maintainer's instruction to skip further
backend work and move to the next step. This is a development experiment using
existing opcodes, with no automatic candidate-discovery or format change.

The existing `mr` development parent is selected before any size comparison.
Verify its manifest SHA-256. Parse its finite-length implicit-VR little-endian
DICOM elements and validate the declared Pixel Data region: byte offset 9092,
length 9,961,472, 19 frames of 512 × 512 unsigned 16-bit samples. Alignment is
relative to the pixel region; its parent offset is divisible by two. No patient
metadata values are included in reports. The region boundaries are determined
from metadata, not compressed sizes. Other development files with unverified
element types are excluded. Sealed files and reserves are excluded.

The full original file is stored in each complete AHPS v5 archive, including
metadata. Fix two segments: original bytes `[0,9092)` as RAW, then the complete
pixel array as one segment. All manual candidates use these same boundaries.
The two-segment RAW control pays its real program-table costs. The unmodified
automatic AHPS encoder is an additional full-file reference with its own actual
selected framing. Use the original task-1 frozen Python/DLL reference, ABI 206.

Freeze 22 methods before measurement: RAW; for each stride 2, 4, 8, 16, DELTA1,
DELTA2, standalone SPLIT, DELTA1 → SPLIT and DELTA2 → SPLIT; then existing AHPS
with explicit `FAST_CHUNKS`. Plain delta parameters are `(stride,0,0,0)`, matching
the existing refinement's plain candidates. These manual methods do not stand
in for automatic/modal-bias planning; the full AHPS reference measures that.

All complete archives use one solid XZ stream, LZMA2 preset 9 plus EXTREME,
64 MiB dictionary, CRC64. Count every physical header and program byte. Run one
serial encode/decode pair per method in listed order, with fresh processes and
fresh restored output paths. Verify every restored file against the original
hash. Record exact archive sizes/hashes, complete process durations, inner
operation times and exact lifetime kernel working-set peaks of each phase's
worker. Single-pass costs are diagnostic; no stable timing ratio is claimed.

Local trials use 64 KiB starting at frame indices 0, 9 and 18, with the same
manual candidates. Report actual raw LZMA2 bytes at preset 1 (the existing local
refinement's trial setting) and preset 9 plus EXTREME, alongside the unchanged C
estimate. Local sizes omit archive framing and cannot replace the complete-file
ranking. Validate inversion of local transforms, and validate native/Python
agreement on short/tail vectors before timing.

Keep this screen bounded: one worker under the shared benchmark lock, 120 seconds
per phase, 600 seconds for the serial screen, and the already validated 8 GiB
Windows Job aggregate commit cap. No repeats, automatic retries or replacement
inputs. Failures and unstarted cases stay visible. These conservative task-5
guards do not extend the skipped backend experiment's budget.

Infrastructure revision recorded during execution: the initial attempt stopped
after 8.390 seconds when the monitor classified a lingering console helper as a
live-descendant failure after a zero-exit encoder. Preserve that attempt and its
original plan. The isolated monitor was corrected to wait for whole-tree exit
under the existing deadline, and an unchanged full comparison cohort was run
under `plan-r2.json`. The original 600-second deadline was retained; no codec,
region, method, size criterion or resource cap changed. The revised monitor's
synthetic aggregate-memory and timeout-tree checks subsequently passed.

Save the input snapshot, exact regions, methods, runtime/source/DLL/harness
hashes and successful preflight checks to `experiments/split-20261103/plan.json`
before the first compression. Do not edit frozen code or plans during a run.

Compare the best standalone SPLIT archive with RAW, the best plain delta, the
best delta-to-SPLIT chain, and existing AHPS. Retain RAW fallback in interpretation
and publish regressions for every stride. This single uint16 development parent
cannot establish float-tensor, other-image or corpus-wide gains. Task 6 requires
a useful fixed-candidate result and remains a separate implementation decision.

The metadata interpretation follows the DICOM standard's
[Implicit VR Little Endian transfer syntax](https://dicom.nema.org/medical/dicom/2024e/output/chtml/part05/chapter_A.html)
and [implicit-VR element layout](https://dicom.nema.org/medical/dicom/2024e/output/chtml/part05/sect_7.1.3.html).
