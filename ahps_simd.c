// ahps_simd.c — v2.5 Native C/AVX2 Kernel with ABI Handshake & OP_EXE_SPLIT (BCJ2-style Plane Lifter)
// Compile (MinGW GCC): gcc -O3 -mavx2 -mpopcnt -shared -o ahps_simd.dll ahps_simd.c
// Compile (MSVC):      cl /O2 /arch:AVX2 /LD ahps_simd.c /Fe:ahps_simd.dll

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#if defined(_MSC_VER)
#include <intrin.h>
#define POPCNT32 __popcnt
#define EXPORT __declspec(dllexport)
#else
#include <immintrin.h>
#define POPCNT32 __builtin_popcount
#define EXPORT
#endif

// Experimental builds set their own ABI so that ahps.py refuses them; see docs/format.md
#if defined(AHPS_EXPERIMENT_NO_MODRM) && (!defined(AHPS_ABI_VERSION) || AHPS_ABI_VERSION != 9206)
#error "AHPS_EXPERIMENT_NO_MODRM changes EXE_SPLIT, so it must build with -DAHPS_ABI_VERSION=9206"
#endif
#ifndef AHPS_ABI_VERSION
#define AHPS_ABI_VERSION 206
#endif

// exe_flags bits for ahps_score_buffer_ex
#define SCORE_EXE32     1
#define SCORE_EXE_SPLIT 2

#define OP_RAW         0x00
#define OP_POLY2       0x01
#define OP_DELTA1_LANE 0x02
#define OP_DELTA2_LANE 0x03
#define OP_EXE_REL32   0x04
#define OP_XORSHIFT16  0x05
#define OP_TRANSPOSE4  0x06
#define OP_SPLIT       0x07
#define OP_EXE32       0x08
#define OP_EXE_SPLIT   0x09

// Planner slot layout, mirrored by the SLOT_* constants in ahps.py
#define NUM_STRIDES    8
#define SLOT_RAW       0
#define SLOT_EXE       1
#define SLOT_DELTA1    2
#define SLOT_DELTA2    (SLOT_DELTA1 + NUM_STRIDES)
#define SLOT_LOCAL     (SLOT_DELTA2 + NUM_STRIDES)
#define NUM_SLOTS      (SLOT_LOCAL + 1)
#define COST_NONE      1e300

static const int STRIDES[NUM_STRIDES] = {1, 2, 4, 8, 12, 16, 24, 32};

// 25173 * 35581 = 895,680,513 = 13667 * 65536 + 1 (Exact mod 2^16 inverse!)
#define LCG_MULT       25173U
#define LCG_INC        13849U
#define LCG_INV_MULT   35581U

static double LOG2_TAB[65537];
static int tab_initialized = 0;

EXPORT int ahps_abi_version(void) {
    return AHPS_ABI_VERSION;
}

EXPORT void ahps_init_tables(void) {
    if (tab_initialized) return;
    LOG2_TAB[0] = 0.0;
    for (int i = 1; i <= 65536; i++) {
        LOG2_TAB[i] = (double)i * log2((double)i);
    }
    tab_initialized = 1;
}

EXPORT double ahps_estimate_mdl_bits(const uint8_t* data, int n, double overhead_bits) {
    if (n <= 0) return 0.0;
    uint32_t counts[256] = {0};
    uint32_t zero_runs = 0;

    int i = 0;
#ifdef __AVX2__
    __m256i zero_vec = _mm256_setzero_si256();
    for (; i <= n - 32; i += 32) {
        __m256i v = _mm256_loadu_si256((const __m256i*)(data + i));
        __m256i cmp = _mm256_cmpeq_epi8(v, zero_vec);
        uint32_t mask = (uint32_t)_mm256_movemask_epi8(cmp);
        zero_runs += POPCNT32(mask & (mask >> 1));
        if (i > 0 && data[i - 1] == 0 && (mask & 1)) zero_runs++;
    }
#endif
    for (int k = 0; k < i; k++) counts[data[k]]++;
    for (; i < n; i++) {
        uint8_t b = data[i];
        counts[b]++;
        if (i > 0 && b == 0 && data[i - 1] == 0) zero_runs++;
    }

    // Collision-free 32-bit 4-gram match counter
    uint32_t hash_val[512];
    uint8_t  hash_valid[512] = {0};
    uint32_t lz4_matches = 0;
    for (int k = 0; k <= n - 4; k += 2) {
        uint32_t w;
        memcpy(&w, data + k, 4);
        if (w != 0) {
            uint32_t h = (w * 2654435761U) >> 23;
            if (hash_valid[h] && hash_val[h] == w) {
                lz4_matches++;
            } else {
                hash_valid[h] = 1;
                hash_val[h] = w;
            }
        }
    }

    double log_n = log2((double)n);
    double entropy_bits = 0.0;
    for (int c = 0; c < 256; c++) {
        uint32_t cnt = counts[c];
        if (cnt > 0) {
            if (cnt <= 65536) {
                entropy_bits += (double)cnt * log_n - LOG2_TAB[cnt];
            } else {
                entropy_bits += (double)cnt * (log_n - log2((double)cnt));
            }
        }
    }

    double discount = (double)zero_runs * 1.15 + (double)lz4_matches * 6.5;
    double net_bits = entropy_bits - discount;
    return overhead_bits + net_bits;
}

static inline int is_rip_rel32_prefix2(uint8_t b0, uint8_t b1) {
    if (b0 == 0x0F && (b1 >= 0x80 && b1 <= 0x8F)) return 1;
    if (b0 == 0xFF && (b1 == 0x15 || b1 == 0x25)) return 1;
    if ((b0 == 0x8B || b0 == 0x8D) && ((b1 & 0xC7) == 0x05)) return 1;
    return 0;
}

static inline uint32_t exe_base_offset(uint8_t opcode, const uint8_t* params) {
    uint32_t base = ((uint32_t)params[0] << 16) | ((uint32_t)params[1] << 8) | (uint32_t)params[2];
    return (opcode == OP_EXE32 || opcode == OP_EXE_SPLIT) ? ((base << 8) | (uint32_t)params[3]) : base;
}

static void split_planes(const uint8_t* src, uint8_t* dst, int n, int s) {
    int idx = 0;
    for (int lane = 0; lane < s; lane++) {
        for (int i = lane; i < n; i += s) dst[idx++] = src[i];
    }
}

static void merge_planes(const uint8_t* src, uint8_t* dst, int n, int s) {
    int idx = 0;
    for (int lane = 0; lane < s; lane++) {
        for (int i = lane; i < n; i += s) dst[i] = src[idx++];
    }
}

// Returns 1 if prev_byte is a common x86_64 opcode where a following 0xE8/0xE9 is a ModRM byte (e.g. 48 89 E8 = MOV RAX, RBP)
static inline int is_modrm_opcode(uint8_t prev) {
#ifdef AHPS_EXPERIMENT_NO_MODRM
    (void)prev;
    return 0;
#endif
    return (prev == 0x89 || prev == 0x8B || prev == 0x8D || prev == 0x83 ||
            prev == 0x81 || prev == 0x39 || prev == 0x3B || prev == 0x01 ||
            prev == 0x03 || prev == 0x29 || prev == 0x2B || prev == 0xFF || prev == 0xF7);
}

// OP_EXE_SPLIT: Segregates E8/E9 4-byte targets into a contiguous 32-bit target table at the tail,
// while also lifting 6-byte RIP-relative Jcc/MOV/LEA instructions in-place in the skeleton!
static void forward_exe_split(uint32_t base_off, const uint8_t* block, uint8_t* res, int n) {
    int k = 0;
    int i = 0;
    uint8_t prev = 0;
    while (i <= n - 5) {
        uint8_t b0 = block[i];
        if ((b0 == 0xE8 || b0 == 0xE9) && !is_modrm_opcode(prev)) {
            k++;
            prev = b0;
            i += 5;
        } else if (i <= n - 6 && is_rip_rel32_prefix2(b0, block[i + 1])) {
            prev = block[i + 5];
            i += 6;
        } else {
            prev = b0;
            i++;
        }
    }

    int skel_len = n - 4 * k;
    uint8_t* tail_targets = res + skel_len;
    int s_idx = 0;
    int c_idx = 0;
    i = 0;
    prev = 0;
    while (i < n) {
        uint8_t b0 = block[i];
        if (i <= n - 5 && (b0 == 0xE8 || b0 == 0xE9) && !is_modrm_opcode(prev)) {
            res[s_idx++] = b0;
            uint32_t rel;
            memcpy(&rel, block + i + 1, 4);
            uint32_t abs_addr = rel + base_off + (uint32_t)i + 5U;
            uint8_t* dst = tail_targets + c_idx * 4;
            dst[0] = (uint8_t)(abs_addr >> 24);
            dst[1] = (uint8_t)(abs_addr >> 16);
            dst[2] = (uint8_t)(abs_addr >> 8);
            dst[3] = (uint8_t)(abs_addr);
            c_idx++;
            prev = b0;
            i += 5;
        } else if (i <= n - 6 && is_rip_rel32_prefix2(b0, block[i + 1])) {
            res[s_idx++] = b0;
            res[s_idx++] = block[i + 1];
            uint32_t rel;
            memcpy(&rel, block + i + 2, 4);
            uint32_t abs_addr = rel + base_off + (uint32_t)i + 6U;
            res[s_idx++] = (uint8_t)(abs_addr >> 24);
            res[s_idx++] = (uint8_t)(abs_addr >> 16);
            res[s_idx++] = (uint8_t)(abs_addr >> 8);
            res[s_idx++] = (uint8_t)(abs_addr);
            prev = block[i + 5];
            i += 6;
        } else {
            res[s_idx++] = b0;
            prev = b0;
            i++;
        }
    }
}

static void inverse_exe_split(uint32_t base_off, const uint8_t* res, uint8_t* out, int n) {
    int k = 0;
    int skel_pos = 0;
    uint8_t prev = 0;
    while (skel_pos + 4 * k < n) {
        int rem_orig = n - (skel_pos + 4 * k);
        uint8_t b0 = res[skel_pos];
        if (rem_orig >= 5 && (b0 == 0xE8 || b0 == 0xE9) && !is_modrm_opcode(prev)) {
            k++;
            prev = b0;
            skel_pos++;
        } else if (rem_orig >= 6 && skel_pos + 6 <= n - 4 * k && is_rip_rel32_prefix2(b0, res[skel_pos + 1])) {
            // Reconstruct original last byte of the 6-byte RIP instruction so `prev` stays 100% synced!
            int orig_i = skel_pos + 4 * k;
            uint32_t abs_addr = ((uint32_t)res[skel_pos + 2] << 24) |
                                ((uint32_t)res[skel_pos + 3] << 16) |
                                ((uint32_t)res[skel_pos + 4] << 8)  |
                                ((uint32_t)res[skel_pos + 5]);
            uint32_t rel = abs_addr - (base_off + (uint32_t)orig_i + 6U);
            prev = (uint8_t)(rel >> 24);
            skel_pos += 6;
        } else {
            prev = b0;
            skel_pos++;
        }
    }

    int skel_len = skel_pos;
    if (skel_len + 4 * k != n) {
        memcpy(out, res, n);
        return;
    }

    const uint8_t* tail_targets = res + skel_len;
    int out_idx = 0;
    int c_idx = 0;
    int s_idx = 0;
    prev = 0;
    while (s_idx < skel_len) {
        uint8_t b0 = res[s_idx];
        if (out_idx <= n - 5 && (b0 == 0xE8 || b0 == 0xE9) && !is_modrm_opcode(prev) && c_idx < k) {
            out[out_idx] = b0;
            const uint8_t* src = tail_targets + c_idx * 4;
            uint32_t abs_addr = ((uint32_t)src[0] << 24) |
                                ((uint32_t)src[1] << 16) |
                                ((uint32_t)src[2] << 8)  |
                                ((uint32_t)src[3]);
            uint32_t rel = abs_addr - (base_off + (uint32_t)out_idx + 5U);
            memcpy(out + out_idx + 1, &rel, 4);
            prev = b0;
            out_idx += 5;
            c_idx++;
            s_idx++;
        } else if (out_idx <= n - 6 && s_idx <= skel_len - 6 && is_rip_rel32_prefix2(b0, res[s_idx + 1])) {
            out[out_idx] = b0;
            out[out_idx + 1] = res[s_idx + 1];
            uint32_t abs_addr = ((uint32_t)res[s_idx + 2] << 24) |
                                ((uint32_t)res[s_idx + 3] << 16) |
                                ((uint32_t)res[s_idx + 4] << 8)  |
                                ((uint32_t)res[s_idx + 5]);
            uint32_t rel = abs_addr - (base_off + (uint32_t)out_idx + 6U);
            memcpy(out + out_idx + 2, &rel, 4);
            prev = out[out_idx + 5];
            out_idx += 6;
            s_idx += 6;
        } else {
            out[out_idx++] = b0;
            prev = b0;
            s_idx++;
        }
    }
}

EXPORT void ahps_forward_lift(uint8_t opcode, const uint8_t* params, const uint8_t* block, uint8_t* res, int n) {
    uint8_t a = params[0], b = params[1], c = params[2];

    if (opcode == OP_SPLIT) {
        split_planes(block, res, n, (a > 0) ? a : 1);
        return;
    }
    if (opcode == OP_EXE_SPLIT) {
        forward_exe_split(exe_base_offset(opcode, params), block, res, n);
        return;
    }

    if (opcode == OP_RAW) {
        memcpy(res, block, n);
    }
    else if (opcode == OP_POLY2) {
        for (int i = 0; i < n; i++) {
            uint8_t pred = (uint8_t)((uint32_t)a * (uint32_t)i * (uint32_t)i + (uint32_t)b * (uint32_t)i + c);
            res[i] = (uint8_t)(block[i] - pred);
        }
    }
    else if (opcode == OP_DELTA1_LANE) {
        int s = (a > 0) ? a : 1;
        for (int i = 0; i < n; i++) {
            res[i] = (i < s) ? block[i] : (uint8_t)(block[i] - (block[i - s] + b));
        }
    }
    else if (opcode == OP_DELTA2_LANE) {
        int s = (a > 0) ? a : 1;
        int two_s = s * 2;
        int i = 0;
        for (; i < two_s && i < n; i++) res[i] = block[i];
#ifdef __AVX2__
        for (; i <= n - 32; i += 32) {
            __m256i b_cur = _mm256_loadu_si256((const __m256i*)(block + i));
            __m256i b_s1  = _mm256_loadu_si256((const __m256i*)(block + i - s));
            __m256i b_s2  = _mm256_loadu_si256((const __m256i*)(block + i - two_s));
            __m256i pred  = _mm256_sub_epi8(_mm256_add_epi8(b_s1, b_s1), b_s2);
            _mm256_storeu_si256((__m256i*)(res + i), _mm256_sub_epi8(b_cur, pred));
        }
#endif
        for (; i < n; i++) {
            uint8_t pred = (uint8_t)(2 * block[i - s] - block[i - two_s]);
            res[i] = (uint8_t)(block[i] - pred);
        }
    }
    else if (opcode == OP_EXE_REL32 || opcode == OP_EXE32) {
        uint32_t base_off = exe_base_offset(opcode, params);
        memcpy(res, block, n);
        int i = 0;
        while (i <= n - 5) {
            uint8_t b0 = block[i];
            if (b0 == 0xE8 || b0 == 0xE9) {
                uint32_t rel;
                memcpy(&rel, block + i + 1, 4);
                uint32_t abs_addr = rel + base_off + (uint32_t)i + 5U;
                res[i + 1] = (uint8_t)(abs_addr >> 24);
                res[i + 2] = (uint8_t)(abs_addr >> 16);
                res[i + 3] = (uint8_t)(abs_addr >> 8);
                res[i + 4] = (uint8_t)(abs_addr);
                i += 5;
            } else if (i <= n - 6 && is_rip_rel32_prefix2(b0, block[i + 1])) {
                uint32_t rel;
                memcpy(&rel, block + i + 2, 4);
                uint32_t abs_addr = rel + base_off + (uint32_t)i + 6U;
                res[i + 2] = (uint8_t)(abs_addr >> 24);
                res[i + 3] = (uint8_t)(abs_addr >> 16);
                res[i + 4] = (uint8_t)(abs_addr >> 8);
                res[i + 5] = (uint8_t)(abs_addr);
                i += 6;
            } else {
                i++;
            }
        }
    }
    else if (opcode == OP_XORSHIFT16) {
        uint16_t state = ((uint16_t)a << 8) | (uint16_t)b;
        if (state == 0) state = 1;
        uint8_t mode = c & 0x03;
        for (int i = 0; i < n; i++) {
            if (mode == 0) {
                state ^= (uint16_t)(state << 7);
                state ^= (uint16_t)(state >> 9);
                state ^= (uint16_t)(state << 8);
            } else if (mode == 1) {
                state = (uint16_t)(state * LCG_MULT + LCG_INC);
            } else {
                state ^= (uint16_t)(state << 5);
                state ^= (uint16_t)(state >> 3);
            }
            res[i] = (uint8_t)(block[i] - (uint8_t)(state >> 8));
        }
    }
    else if (opcode == OP_TRANSPOSE4) {
        int words = n / 4, rem = n % 4, idx = 0;
        for (int lane = 0; lane < 4; lane++) {
            uint8_t prev = 0;
            for (int w = 0; w < words; w++) {
                uint8_t val = block[w * 4 + lane];
                res[idx++] = (uint8_t)(val - prev);
                prev = val;
            }
        }
        for (int r = 0; r < rem; r++) res[idx++] = block[words * 4 + r];
    }
}

EXPORT void ahps_inverse_lift(uint8_t opcode, const uint8_t* params, const uint8_t* res, uint8_t* out, int n) {
    uint8_t a = params[0], b = params[1], c = params[2];

    if (opcode == OP_SPLIT) {
        merge_planes(res, out, n, (a > 0) ? a : 1);
        return;
    }
    if (opcode == OP_EXE_SPLIT) {
        inverse_exe_split(exe_base_offset(opcode, params), res, out, n);
        return;
    }

    if (opcode == OP_RAW) {
        memcpy(out, res, n);
    }
    else if (opcode == OP_POLY2) {
        for (int i = 0; i < n; i++) {
            uint8_t pred = (uint8_t)((uint32_t)a * (uint32_t)i * (uint32_t)i + (uint32_t)b * (uint32_t)i + c);
            out[i] = (uint8_t)(res[i] + pred);
        }
    }
    else if (opcode == OP_DELTA1_LANE) {
        int s = (a > 0) ? a : 1;
        for (int i = 0; i < n; i++) {
            out[i] = (i < s) ? res[i] : (uint8_t)(res[i] + out[i - s] + b);
        }
    }
    else if (opcode == OP_DELTA2_LANE) {
        int s = (a > 0) ? a : 1;
        int two_s = s * 2;
        for (int i = 0; i < n; i++) {
            out[i] = (i < two_s) ? res[i] : (uint8_t)(res[i] + 2 * out[i - s] - out[i - two_s]);
        }
    }
    else if (opcode == OP_EXE_REL32 || opcode == OP_EXE32) {
        uint32_t base_off = exe_base_offset(opcode, params);
        memcpy(out, res, n);
        int i = 0;
        while (i <= n - 5) {
            uint8_t b0 = out[i];
            if (b0 == 0xE8 || b0 == 0xE9) {
                uint32_t abs_addr = ((uint32_t)out[i + 1] << 24) |
                                    ((uint32_t)out[i + 2] << 16) |
                                    ((uint32_t)out[i + 3] << 8)  |
                                    ((uint32_t)out[i + 4]);
                uint32_t rel = abs_addr - (base_off + (uint32_t)i + 5U);
                memcpy(out + i + 1, &rel, 4);
                i += 5;
            } else if (i <= n - 6 && is_rip_rel32_prefix2(b0, out[i + 1])) {
                uint32_t abs_addr = ((uint32_t)out[i + 2] << 24) |
                                    ((uint32_t)out[i + 3] << 16) |
                                    ((uint32_t)out[i + 4] << 8)  |
                                    ((uint32_t)out[i + 5]);
                uint32_t rel = abs_addr - (base_off + (uint32_t)i + 6U);
                memcpy(out + i + 2, &rel, 4);
                i += 6;
            } else {
                i++;
            }
        }
    }
    else if (opcode == OP_XORSHIFT16) {
        uint16_t state = ((uint16_t)a << 8) | (uint16_t)b;
        if (state == 0) state = 1;
        uint8_t mode = c & 0x03;
        for (int i = 0; i < n; i++) {
            if (mode == 0) {
                state ^= (uint16_t)(state << 7);
                state ^= (uint16_t)(state >> 9);
                state ^= (uint16_t)(state << 8);
            } else if (mode == 1) {
                state = (uint16_t)(state * LCG_MULT + LCG_INC);
            } else {
                state ^= (uint16_t)(state << 5);
                state ^= (uint16_t)(state >> 3);
            }
            out[i] = (uint8_t)(res[i] + (uint8_t)(state >> 8));
        }
    }
    else if (opcode == OP_TRANSPOSE4) {
        int words = n / 4, rem = n % 4, idx = 0;
        for (int lane = 0; lane < 4; lane++) {
            uint8_t prev = 0;
            for (int w = 0; w < words; w++) {
                uint8_t val = (uint8_t)(res[idx++] + prev);
                out[w * 4 + lane] = val;
                prev = val;
            }
        }
        for (int r = 0; r < rem; r++) out[words * 4 + r] = res[idx++];
    }
}

static void try_local(uint8_t op, const uint8_t* prm, const uint8_t* block, int n, uint8_t* work,
                      double* best, uint8_t* local) {
    ahps_forward_lift(op, prm, block, work, n);
    double cost = ahps_estimate_mdl_bits(work, n, 0.0);
    if (cost < *best) {
        *best = cost;
        local[0] = op;
        memcpy(local + 1, prm, 4);
    }
}

static void score_block(const uint8_t* block, int n, uint32_t block_offset, int exe_flags,
                        double* costs, uint8_t* local, uint8_t* work) {
    double raw_bits = ahps_estimate_mdl_bits(block, n, 0.0);
    costs[SLOT_RAW] = raw_bits - fabs(raw_bits) * 0.025;

    uint8_t prm[4] = {
        (uint8_t)(block_offset >> 24), (uint8_t)(block_offset >> 16),
        (uint8_t)(block_offset >> 8), (uint8_t)block_offset
    };
    double c_exe = COST_NONE;
    if (exe_flags & (SCORE_EXE32 | SCORE_EXE_SPLIT)) {
        // Fast scan: check if the block contains any E8/E9 or 2-byte RIP-relative opcode before lifting
        int has_exe_op = 0;
        for (int i = 0; i <= n - 5; i++) {
            uint8_t b0 = block[i];
            if (b0 == 0xE8 || b0 == 0xE9 || (i <= n - 6 && is_rip_rel32_prefix2(b0, block[i + 1]))) {
                has_exe_op = 1;
                break;
            }
        }
        if (!has_exe_op) {
            c_exe = raw_bits;
        } else {
            if (exe_flags & SCORE_EXE32) {
                ahps_forward_lift(OP_EXE32, prm, block, work, n);
                c_exe = ahps_estimate_mdl_bits(work, n, 0.0);
            }
            if (exe_flags & SCORE_EXE_SPLIT) {
                ahps_forward_lift(OP_EXE_SPLIT, prm, block, work, n);
                double c_exesp = ahps_estimate_mdl_bits(work, n, 0.0);
                if (c_exesp < c_exe) c_exe = c_exesp;
            }
        }
    }
    costs[SLOT_EXE] = c_exe;

    double best_local = COST_NONE;
    memset(local, 0, 5);

    // Strided 1st & 2nd Order Linear Predictors
    for (int si = 0; si < NUM_STRIDES; si++) {
        int s = STRIDES[si];
        if (n < s * 4) {
            costs[SLOT_DELTA1 + si] = raw_bits;
            costs[SLOT_DELTA2 + si] = raw_bits;
            continue;
        }
        prm[0] = (uint8_t)s; prm[1] = 0; prm[2] = 0; prm[3] = 0;
        ahps_forward_lift(OP_DELTA1_LANE, prm, block, work, n);
        costs[SLOT_DELTA1 + si] = ahps_estimate_mdl_bits(work, n, 0.0);
        ahps_forward_lift(OP_DELTA2_LANE, prm, block, work, n);
        costs[SLOT_DELTA2 + si] = ahps_estimate_mdl_bits(work, n, 0.0);

        uint16_t d_freq[256] = {0};
        uint8_t modal_d = 0;
        uint16_t max_f = 0;
        int lim = (n < s * 16) ? n : s * 16;
        for (int i = s; i < lim; i++) {
            uint8_t d = (uint8_t)(block[i] - block[i - s]);
            if (++d_freq[d] > max_f) {
                max_f = d_freq[d];
                modal_d = d;
            }
        }
        if (modal_d != 0) {
            prm[1] = modal_d;
            try_local(OP_DELTA1_LANE, prm, block, n, work, &best_local, local);
        }
    }

    // Algebraic Finite-Difference Polynomial Solver
    if (n >= 4) {
        uint8_t d1_0 = (uint8_t)(block[1] - block[0]);
        uint8_t d1_1 = (uint8_t)(block[2] - block[1]);
        uint8_t d2_0 = (uint8_t)(d1_1 - d1_0);
        if ((d2_0 & 1) == 0) {
            // The other root adds 128*i*(i-1), which is zero modulo 256.
            uint8_t a_cand = (uint8_t)(d2_0 >> 1);
            prm[0] = a_cand; prm[1] = (uint8_t)(d1_0 - a_cand); prm[2] = block[0]; prm[3] = 0;
            try_local(OP_POLY2, prm, block, n, work, &best_local, local);
        }
    }

    // Exact O(256) Modular Inverse Seed Solver (LCG_INV_MULT = 35581U) with 4-Byte Prefix Filter
    if (n >= 32) {
        uint8_t b0 = block[0], b1 = block[1], b2 = block[2], b3 = block[3];
        for (int s1_lo = 0; s1_lo < 256; s1_lo++) {
            uint16_t s1 = (uint16_t)(((uint16_t)b0 << 8) | (uint16_t)s1_lo);
            uint16_t s2 = (uint16_t)(s1 * LCG_MULT + LCG_INC);
            if ((s2 >> 8) == b1) {
                uint16_t s3 = (uint16_t)(s2 * LCG_MULT + LCG_INC);
                uint16_t s4 = (uint16_t)(s3 * LCG_MULT + LCG_INC);
                int diff2 = (int)(s3 >> 8) - (int)b2;
                int diff3 = (int)(s4 >> 8) - (int)b3;
                if (diff2 >= -2 && diff2 <= 2 && diff3 >= -2 && diff3 <= 2) {
                    uint16_t seed = (uint16_t)((uint16_t)(s1 - LCG_INC) * LCG_INV_MULT);
                    if (seed != 0) {
                        prm[0] = (uint8_t)(seed >> 8); prm[1] = (uint8_t)(seed & 0xFF); prm[2] = 1; prm[3] = 0;
                        try_local(OP_XORSHIFT16, prm, block, n, work, &best_local, local);
                    }
                }
            }
        }
    }

    costs[SLOT_LOCAL] = best_local;
}

EXPORT int ahps_score_buffer_ex(
    const uint8_t* data,
    size_t total_len,
    int block_size,
    int exe_flags,
    double* out_costs,
    uint8_t* out_local
) {
    ahps_init_tables();
    uint8_t* work = (uint8_t*)malloc((size_t)block_size);
    if (!work) return 0;

    size_t num_blocks = (total_len + (size_t)block_size - 1) / (size_t)block_size;
    for (size_t idx = 0; idx < num_blocks; idx++) {
        size_t off = idx * (size_t)block_size;
        int blk_len = (off + (size_t)block_size <= total_len) ? block_size : (int)(total_len - off);
        score_block(data + off, blk_len, (uint32_t)off, exe_flags,
                    out_costs + idx * NUM_SLOTS, out_local + idx * 5, work);
    }
    free(work);
    return 1;
}

EXPORT int ahps_score_buffer(
    const uint8_t* data,
    size_t total_len,
    int block_size,
    double* out_costs,
    uint8_t* out_local
) {
    return ahps_score_buffer_ex(data, total_len, block_size, SCORE_EXE32 | SCORE_EXE_SPLIT, out_costs, out_local);
}

EXPORT void ahps_unlift_entire_buffer(
    const uint8_t* opcodes,
    const uint8_t* active_params,
    size_t num_blocks,
    size_t total_len,
    int block_size,
    const uint8_t* residual,
    uint8_t* out_data
) {
    size_t param_cursor = 0;
    const uint8_t zero_params[4] = {0, 0, 0, 0};

    for (size_t idx = 0; idx < num_blocks; idx++) {
        size_t off = idx * (size_t)block_size;
        int blk_len = (off + (size_t)block_size <= total_len) ? block_size : (int)(total_len - off);
        uint8_t op = opcodes[idx];
        uint8_t prm[4] = {0, 0, 0, 0};
        if (op != OP_RAW && active_params != NULL) {
            memcpy(prm, active_params + param_cursor, 3);
            param_cursor += 3;
        }
        ahps_inverse_lift(op, (op != OP_RAW) ? prm : zero_params, residual + off, out_data + off, blk_len);
    }
}
