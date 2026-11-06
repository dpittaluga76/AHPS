/* AHPS rANS ABI 1: exact FP8 magnitude coding with a packed sign stream.
 * Frequencies are supplied by the container and sum to 4096.
 * All state is local to the call. No floating-point conversion is performed.
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>

#if defined(_WIN32)
#define RANS_API __declspec(dllexport)
#else
#define RANS_API __attribute__((visibility("default")))
#endif

#define INPUT_CAP ((size_t)32 * 1024 * 1024)
#define SCALE_BITS 12
#define SCALE_TOTAL 4096u
#define RANS_L (UINT32_C(1) << 23)

enum { RANS_OK = 0, RANS_INVALID = 1, RANS_CAPACITY = 2 };

static int valid_buffer(const void *pointer, size_t length) {
    return length == 0 || (pointer != NULL && length <= UINTPTR_MAX - (uintptr_t)pointer);
}

static int overlaps(const void *left, size_t left_size, const void *right, size_t right_size) {
    if (left_size == 0 || right_size == 0) return 0;
    return (uintptr_t)left < (uintptr_t)right + right_size &&
           (uintptr_t)right < (uintptr_t)left + left_size;
}

static uint32_t read32(const uint8_t *source) {
    return (uint32_t)source[0] | ((uint32_t)source[1] << 8) |
           ((uint32_t)source[2] << 16) | ((uint32_t)source[3] << 24);
}

static void write32(uint8_t *destination, uint32_t value) {
    unsigned index;
    for (index = 0; index < 4; ++index) destination[index] = (uint8_t)(value >> (8 * index));
}

static int make_tables(const uint16_t *frequencies, size_t count,
                       uint16_t cumulative[128], uint8_t *symbols) {
    unsigned symbol, total = 0;
    if (count != 128 || !valid_buffer(frequencies, 128 * sizeof(*frequencies))) return RANS_INVALID;
    for (symbol = 0; symbol < 128; ++symbol) {
        unsigned frequency = frequencies[symbol];
        if (frequency > SCALE_TOTAL - total) return RANS_INVALID;
        cumulative[symbol] = (uint16_t)total;
        if (symbols != NULL) memset(symbols + total, (int)symbol, frequency);
        total += frequency;
    }
    return total == SCALE_TOTAL ? RANS_OK : RANS_INVALID;
}

RANS_API uint32_t ahps_rans_abi_version(void) { return 1; }

RANS_API int ahps_rans_encode(const uint8_t *source, size_t size,
                             const uint16_t *frequencies, size_t frequency_count,
                             uint8_t *output, size_t capacity, size_t *written) {
    uint16_t cumulative[128];
    uint32_t state = RANS_L;
    size_t sign_bytes, position, index, left, right;
    if (!valid_buffer(written, sizeof(*written)) || size > INPUT_CAP ||
            frequency_count != 128 || !valid_buffer(frequencies, 128 * sizeof(*frequencies)) ||
            !valid_buffer(source, size) || !valid_buffer(output, capacity)) return RANS_INVALID;
    if (overlaps(written, sizeof(*written), source, size) ||
            overlaps(written, sizeof(*written), frequencies, 128 * sizeof(*frequencies)) ||
            overlaps(written, sizeof(*written), output, capacity)) return RANS_INVALID;
    *written = 0;
    if (overlaps(source, size, output, capacity)) return RANS_INVALID;
    if (make_tables(frequencies, frequency_count, cumulative, NULL) != RANS_OK ||
            overlaps(frequencies, 128 * sizeof(*frequencies), output, capacity)) return RANS_INVALID;
    sign_bytes = (size + 7) / 8;
    if (capacity < sign_bytes + 4) return RANS_CAPACITY;
    memset(output, 0, sign_bytes);
    position = sign_bytes + 4;
    for (index = size; index != 0;) {
        unsigned value, magnitude, frequency;
        uint32_t maximum;
        --index;
        value = source[index];
        magnitude = value & 127;
        frequency = frequencies[magnitude];
        if (frequency == 0) return RANS_INVALID;
        output[index >> 3] |= (uint8_t)((value >> 7) << (index & 7));
        maximum = ((RANS_L >> SCALE_BITS) << 8) * frequency;
        while (state >= maximum) {
            if (position >= capacity) return RANS_CAPACITY;
            output[position++] = (uint8_t)state;
            state >>= 8;
        }
        state = ((state / frequency) << SCALE_BITS) + state % frequency + cumulative[magnitude];
    }
    write32(output + sign_bytes, state);
    left = sign_bytes + 4;
    right = position;
    while (left < right) {
        uint8_t value;
        --right;
        value = output[left];
        output[left++] = output[right];
        output[right] = value;
    }
    *written = position;
    return RANS_OK;
}

RANS_API int ahps_rans_decode(const uint8_t *payload, size_t payload_size, size_t size,
                             const uint16_t *frequencies, size_t frequency_count,
                             uint8_t *output, size_t capacity) {
    uint16_t cumulative[128];
    uint8_t symbols[SCALE_TOTAL];
    uint32_t state;
    size_t sign_bytes, position, index;
    if (size > INPUT_CAP) return RANS_INVALID;
    sign_bytes = (size + 7) / 8;
    if (payload_size < sign_bytes + 4 || payload_size > sign_bytes + 2 * size + 4 ||
            !valid_buffer(payload, payload_size) || !valid_buffer(output, capacity) ||
            overlaps(payload, payload_size, output, capacity)) return RANS_INVALID;
    if (capacity < size) return RANS_CAPACITY;
    if (make_tables(frequencies, frequency_count, cumulative, symbols) != RANS_OK ||
            overlaps(frequencies, 128 * sizeof(*frequencies), output, capacity)) return RANS_INVALID;
    if ((size & 7) && (payload[sign_bytes - 1] >> (size & 7))) return RANS_INVALID;
    state = read32(payload + sign_bytes);
    if (state < RANS_L || state >= (RANS_L << 8)) return RANS_INVALID;
    position = sign_bytes + 4;
    for (index = 0; index < size; ++index) {
        unsigned slot = state & (SCALE_TOTAL - 1);
        unsigned magnitude = symbols[slot];
        unsigned sign = (payload[index >> 3] >> (index & 7)) & 1;
        output[index] = (uint8_t)(magnitude | (sign << 7));
        state = frequencies[magnitude] * (state >> SCALE_BITS) + slot - cumulative[magnitude];
        while (state < RANS_L) {
            if (position >= payload_size) return RANS_INVALID;
            state = (state << 8) | payload[position++];
        }
    }
    return state == RANS_L && position == payload_size ? RANS_OK : RANS_INVALID;
}
