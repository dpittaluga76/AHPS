# AHPS archive format

This is the contract between the AHPS writer and every reader. A change to any byte layout or lift below needs a new magic. Planner and search changes don't, as long as decoding stays the same.

For commands and memory behavior, see [usage](usage.md). Optional native
libraries are built from source using [the build guide](building.md). Terminal
progress and AHPS-only benchmark selection do not change serialized formats.

## Outer stream

An archive is exactly one XZ stream. It holds a *container*, described below. Bytes after the end of that stream are an error.

Integers are little-endian unless stated otherwise.

## Versions

| Magic | Tool | Writer | Reader |
| --- | --- | --- | --- |
| `AHPS\x07` | Safetensors-enabled tool | With `--safetensors`, when smaller | Yes, with safetensors-enabled tool |
| `AHPS\x06` | CSV-enabled tool | With `--csv`, when smaller | Yes, with CSV-enabled tool |
| `AHPS\x05` | v2.6 and later | Yes | Yes |
| `AHPS\x04` | v2.4, v2.5 | No | Yes, see below |
| `AHPS\x03` | v2.3 | No | Yes |

## Tensor container v7

The optional safetensors candidate retains the single outer XZ stream and uses
a variable-length payload for each segment. Earlier readers reject its magic.
The updated reader also accepts the earlier formats listed above.

```
Header (25 bytes): [Magic 5B][NumSegments u32][TableLen u32][TotalLen u64][CRC32 u32]
Table: NumSegments x [Mode u8][RestoredLength u32][PayloadLength u32]
Payload: segment payloads concatenated in table order
```

`TotalLen` is the original input size. `CRC32` covers the complete restored
bytes. The table contains fixed nine-byte records and must have exactly
`9 * NumSegments` bytes. Readers require between 1 and 65,536 segments, each
with a positive restored length, and at most 32 MiB of total restored data.
The sum of restored segment lengths must equal `TotalLen`. The sum of payload
lengths plus `25 + TableLen` must equal the actual container length. Readers
validate these relations before allocating restored segment buffers.

| Mode | Name | Payload and reconstruction |
| --- | --- | --- |
| `0` | RAW | Literal bytes. `PayloadLength` equals `RestoredLength` |
| `1` | F32_LOW16_ZERO | Two upper-byte planes. `RestoredLength` is divisible by four and `PayloadLength` equals half that length |
| `2` | F8_RANS | A 256-byte magnitude frequency table, followed by packed signs and an rANS stream. The complete payload must be smaller than `RestoredLength` |

Unknown modes are rejected. RAW covers the source header, other supported
dtypes, ineligible tensor spans, and partial F32 values at chunk edges.

For mode 1, let `N = RestoredLength / 4`. The first `N` payload bytes are byte 2
of each original little-endian F32 word, and the next `N` bytes are byte 3.
Reconstruction sets bytes 0 and 1 of every word to zero and restores the two
upper bytes. The encoder verifies the two discarded bytes are zero in every
word of the selected tensor span. This preserves the original bit patterns
without any floating-point conversion.

For mode 2, each original byte is represented by its sign bit, `byte >> 7`,
and magnitude symbol, `byte & 127`. The segment payload has this layout.

```
[128 x Frequency u16][ceil(N/8) sign bytes][State u32][Renormalization bytes]
```

Here `N = RestoredLength`. Frequencies are indexed by magnitude symbol and sum
to 4096. Zero frequencies denote unused symbols. Sign bit `i` occupies bit
`i % 8` of sign byte `i // 8`, with unused high bits in the last sign byte set
to zero. This retains negative-zero signs and every other original byte code.

The rANS state uses `L = 2^23` and a 12-bit frequency scale. Cumulative
frequencies are exclusive prefix sums in ascending symbol order. The encoder
starts with state `L` and processes magnitude symbols in reverse input order.
For a symbol with frequency `f` and cumulative frequency `c`, it emits the low
state byte and shifts the state right by eight while
`state >= ((L >> 12) << 8) * f`. It then updates the state to
`((state // f) << 12) + state % f + c`. The final state is stored little-endian,
followed by the emitted bytes in reverse order.

The decoder finds the symbol whose cumulative interval contains
`slot = state & 4095`, combines it with the next stored sign, and updates the
state to `f * (state >> 12) + slot - c`. While the state is below `L`, it reads
the next renormalization byte and updates `state = (state << 8) | byte`.
The initial stored state must be in `[L, L * 256)`. After exactly `N` symbols,
the state must equal `L` and every renormalization byte must be consumed.
Invalid frequency totals, sign padding, states, truncated streams, and trailing
bytes are rejected. The restored container CRC is checked after all segments.

The encoder normalizes each FP8 tensor span's observed magnitude counts to
4096 using integer floors and a minimum frequency of one for observed symbols.
It adjusts the total by the largest remaining count residual when adding
frequency, or the smallest residual among frequencies above one when removing
it. Ties use the lowest symbol index. The decoder uses the stored frequencies.

Layout parsing reads at most the eight-byte safetensors header length and a
16 MiB JSON header. It rejects duplicate keys, invalid shapes, unknown dtype
widths, and ranges that overlap, leave gaps, or fail to cover the source data.
Shapes and dtype widths must match their declared byte ranges. Mode 2 applies
to `F8_E4M3`, while mode 1 applies to aligned `F32` spans. Other recognized
dtypes use RAW. Chunk offsets refer to absolute bytes in the original source,
including its header. The completed AHPS archive carries everything needed to
restore the selected input slice.

The candidate is emitted only when a segment changes representation, and is
selected only when its complete XZ archive is smaller than the usual AHPS
archive at the same resolved preset. Public decoder limits also apply. Both
decode APIs buffer the full v7 container, and file decoding replaces the
destination after all checks pass. The 32 MiB v7 limit is a format bound that
cannot be raised through the general decoder limit arguments.

The optional `ahps_rans` native library uses ABI 1 and produces the same byte
format as the Python implementation. It leaves the existing planner/lift ABI
206 unchanged.

## CSV container v6

The optional CSV candidate uses a distinct container while retaining the single
outer XZ stream. Earlier readers reject this magic. The native ABI is unchanged.

```
Header (25 bytes): [Magic 5B][Rows u32][TableLen u32][TotalLen u64][CRC32 u32]
Table: [Columns u32] then Rows * Columns x [SpanLength u32], in row-major order
Residual (TotalLen bytes): raw cell spans, in column-major order
```

Each span includes the original field bytes and its following comma or record
terminator. The final span can end at EOF and can be empty. An initial UTF-8 BOM
belongs to the first span. Quotes and escaping remain verbatim. The transform
preserves length and reconstructs the original byte order without CSV reformatting.

Readers require at least two rows and two columns, at most 1,024 columns, and at
most 1,000,000 fields. `TableLen` must equal `4 + 4 * Rows * Columns`, all span
lengths must sum to `TotalLen`, and `25 + TableLen + TotalLen` must equal the
container length. The CRC-32 covers the restored bytes. The length table may
contain zero-length spans. All existing decoder limits apply, including the
early declared-size checks. Both public decode APIs buffer the full transformed
container for v6. File decoding still replaces the destination only after all
validation succeeds.

The encoder accepts consistent comma-separated records with double-quote
escaping, CRLF, LF, or CR record endings, and quoted multiline fields. Its
initial logical-row probe is bounded to 64 KiB. Unsupported inputs decline the
candidate. The candidate is selected only when its complete compressed archive
is smaller than the usual AHPS result at the same resolved preset.

## Container v4 and v5

The layout is the same in both versions.

```
Header (25 bytes): [Magic 5B][NumSegments u32][TableLen u32][TotalLen u64][CRC32 u32]
Table (TableLen bytes), once per segment: [Length u32][NumSteps u8] then NumSteps x [Opcode u8][Params 4B]
Residual (TotalLen bytes): the lifted segments, in table order
```

`CRC32` covers the restored data. Readers must reject a container unless all of these hold:

- `25 + TableLen + TotalLen` equals the container length.
- Every segment has `1 <= Length <= 2^30` and `NumSteps <= 3`.
- Every step opcode is in the opcode table below, and is neither `0x00` nor `0x04`. A RAW segment has zero steps.
- The table ends exactly at `25 + TableLen`, and the segment lengths sum to `TotalLen`.

Lifts preserve length. To decode a segment, apply the inverse of each step, last step first.

## Container v3

```
Header (17 bytes): [Magic 5B][NumBlocks u32][BlockSize u32][ParamLen u32]
Opcodes (NumBlocks bytes), Params (ParamLen bytes), Residual (the remainder)
```

Each block has one opcode, up to `0x06`, and each non-RAW block has 3 parameter bytes. Readers must check that `1 <= BlockSize <= 2^31 - 1`, that `NumBlocks` equals the residual length divided by `BlockSize` rounded up, and that `ParamLen` equals 3 times the non-RAW block count. v3 has no checksum.

## Opcodes

| Opcode | Name | Params | Lift, for byte `i` of a segment |
| --- | --- | --- | --- |
| `0x00` | RAW | Unused | Copy. v3 blocks only |
| `0x01` | POLY2 | `a b c` | `x[i] - (a*i*i + b*i + c)` |
| `0x02` | DELTA1 | `s d` | `x[i] - (x[i-s] + d)`, first `s` bytes copied |
| `0x03` | DELTA2 | `s` | `x[i] - (2*x[i-s] - x[i-2s])`, first `2s` bytes copied |
| `0x04` | EXE_REL32 | 24-bit base, big-endian | v3 only, as EXE32 |
| `0x05` | XORSHIFT16 | seed big-endian in 2 bytes, then mode | `x[i] - (state >> 8)`, state stepped first |
| `0x06` | TRANSPOSE4 | Unused | Four lanes, each delta coded, remainder copied |
| `0x07` | SPLIT | `s` | Lanes `0` to `s-1` written one after another |
| `0x08` | EXE32 | 32-bit base, big-endian | Inline rewrite, see below |
| `0x09` | EXE_SPLIT | 32-bit base, big-endian | Separate target table, see below |

Arithmetic is modulo 256, and `s = 0` means `s = 1`. The XORSHIFT16 mode is its third parameter byte `& 3`: `0` is 7/9/8 xorshift, `1` is the LCG `state * 25173 + 13849`, and `2` and `3` are both 5/3 xorshift. A zero seed means 1.

## x86 lifts

Both lifts scan left to right and rewrite relative targets as absolute big-endian words: `abs = rel + base + i + len`, modulo 2^32, where `len` is the instruction length.

A 6-byte form is `0F 80..8F`, `FF 15`, `FF 25`, or `8B`/`8D` with `(modrm & 0xC7) == 0x05`. Its 4 target bytes start at `i + 2`.

**EXE32** rewrites `E8`/`E9` at `i <= n-5` and 6-byte forms at `i <= n-6`, in place.

**EXE_SPLIT** keeps a `prev` byte, starting at 0.

- `E8`/`E9` at `i <= n-5` is lifted unless `prev` is one of `89 8B 8D 83 81 39 3B 01 03 29 2B FF F7`. The opcode stays in the skeleton, its 4-byte target goes to the next word of the target table, `prev` becomes the opcode, and the scan moves 5 bytes.
- Otherwise, a 6-byte form at `i <= n-6` stays in the skeleton with its target rewritten in place. `prev` becomes the original byte `i + 5`, and the scan moves 6 bytes.
- Any other byte is copied, and `prev` becomes that byte.

The residual is the skeleton followed by the target table. The reader finds the split by rerunning this scan over the skeleton. Every residual decodes, so EXE_SPLIT has no invalid input of its own.

## `AHPS\x04` with opcode `0x09`

Two incompatible meanings of opcode `0x09` shipped under `AHPS\x04` and ABI 205. The later one is the EXE_SPLIT above. The earlier one predates the `prev` exclusion and the contiguous target words.

Readers support only the later meaning and never try a second decoder. An archive of the earlier kind may fail with the CRC error, which reports corruption or an unsupported older archive. Some inputs transform identically under both meanings, so such an archive can also decode correctly. CRC-32 is an integrity check and does not identify a format. Recover failing archives with the tool version that wrote them.

Native libraries report their ABI through `ahps_abi_version()`. ABI 206 means the semantics in this document. Readers ignore any other ABI and use the Python engine. Experimental builds that change a lift must set their own ABI and must never write archives.

## Decoder limits

Limits apply to every version and are enforced while the stream is decoded.

| Limit | Default | Applies to |
| --- | --- | --- |
| `max_dict_bytes` | 256 MiB | liblzma's decoder working memory |
| `max_container_bytes` | 4 GiB + 1 MiB | Total decoded container bytes |
| `max_output_bytes` | 4 GiB | Restored bytes, before allocation |

`max_dict_bytes` is the liblzma decoder memlimit. It does not bound peak process memory, which also holds the decoded container and the restored data.

`ahps_decompress_bytes` holds the archive, the container and the restored data. For v4 and v5, `ahps_decompress_file`, which the CLI uses, reads the archive 64 KiB at a time and holds one transformed segment at a time, with RAW segments restored 1 MiB at a time, under the same limits and validation. It writes to a temporary file beside the output and replaces the output only after the checksum passes, so a rejected archive leaves any existing file unchanged. v3, v6 and v7 archives buffer the complete container for reconstruction.

Exceeding a limit is an error, and callers can raise a limit explicitly. An archive that exceeds a default is not corrupt. A negative limit, or a `max_dict_bytes` of zero, is rejected before decoding starts. A `max_output_bytes` of zero still allows an empty archive.
