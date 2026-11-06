/* Independent AHRC v1: locally authored LZ77 and binary arithmetic coding.
 * Wire/model contract: docs/independent-backend-experiment.md.
 * No external compression library or production-codec entry point is used.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#if defined(_WIN32)
#define AHRC_API __declspec(dllexport)
#else
#define AHRC_API __attribute__((visibility("default")))
#endif

#define SOURCE_CAP ((size_t)128 * 1024 * 1024)
#define TABLE_CAP ((size_t)8 * 1024 * 1024)
#define WINDOW UINT32_C(8388608)
#define WINDOW_MASK (WINDOW - 1)
#define HASH_SIZE UINT32_C(524288)
#define NO_POS UINT32_MAX
#define MAX_MATCH UINT32_C(65535)
#define HALF UINT32_C(0x80000000)
#define QUARTER UINT32_C(0x40000000)
#define THREE_QUARTERS UINT32_C(0xc0000000)

enum { OK = 0, INVALID = 1, CAPACITY = 2, ALLOCATION = 3 };

typedef struct {
    uint16_t prefix[24];
    uint16_t suffix[24][24];
} IntegerModel;

typedef struct {
    uint16_t token[8];
    uint16_t repeat[4];
    uint16_t literal[256][256];
    IntegerModel length;
    IntegerModel distance;
} Models;

typedef struct {
    uint8_t *data;
    size_t capacity;
    uint64_t bits;
    uint64_t pending;
    uint32_t low;
    uint32_t high;
} Encoder;

typedef struct {
    const uint8_t *data;
    uint64_t bits;
    uint64_t used;
    uint32_t low;
    uint32_t high;
    uint32_t code;
} Decoder;

static uint32_t read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void write32(uint8_t *p, uint32_t value) {
    unsigned i;
    for (i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (8 * i));
}

static uint64_t read64(const uint8_t *p) {
    return (uint64_t)read32(p) | ((uint64_t)read32(p + 4) << 32);
}

static void write64(uint8_t *p, uint64_t value) {
    write32(p, (uint32_t)value);
    write32(p + 4, (uint32_t)(value >> 32));
}

static int valid_buffer(const void *p, size_t length) {
    if (length == 0) return 1;
    return p != NULL && length <= UINTPTR_MAX - (uintptr_t)p;
}

static int overlaps(const void *a, size_t na, const void *b, size_t nb) {
    if (na == 0 || nb == 0) return 0;
    return (uintptr_t)a < (uintptr_t)b + nb &&
           (uintptr_t)b < (uintptr_t)a + na;
}

static void fill_probabilities(uint16_t *p, size_t count) {
    size_t i;
    for (i = 0; i < count; ++i) p[i] = 2048;
}

static void init_integer(IntegerModel *model) {
    unsigned i;
    fill_probabilities(model->prefix, 24);
    for (i = 0; i < 24; ++i) fill_probabilities(model->suffix[i], 24);
}

static Models *new_models(void) {
    Models *models = (Models *)malloc(sizeof(*models));
    unsigned i;
    if (models == NULL) return NULL;
    fill_probabilities(models->token, 8);
    fill_probabilities(models->repeat, 4);
    for (i = 0; i < 256; ++i) fill_probabilities(models->literal[i], 256);
    init_integer(&models->length);
    init_integer(&models->distance);
    return models;
}

static void update_probability(uint16_t *probability, unsigned bit) {
    uint32_t p = *probability;
    *probability = (uint16_t)(bit ? p - (p >> 5) : p + ((4096 - p) >> 5));
}

static int put_bit(Encoder *encoder, unsigned bit) {
    uint64_t byte = encoder->bits >> 3;
    unsigned offset = (unsigned)(encoder->bits & 7);
    if (byte >= encoder->capacity) return CAPACITY;
    if (offset == 0) encoder->data[(size_t)byte] = 0;
    encoder->data[(size_t)byte] |= (uint8_t)(bit << (7 - offset));
    ++encoder->bits;
    return OK;
}

static int emit_with_pending(Encoder *encoder, unsigned bit) {
    int status = put_bit(encoder, bit);
    if (status != OK) return status;
    while (encoder->pending != 0) {
        status = put_bit(encoder, bit ^ 1);
        if (status != OK) return status;
        --encoder->pending;
    }
    return OK;
}

static int encode_bit(Encoder *encoder, uint16_t *probability, unsigned bit) {
    uint64_t range = (uint64_t)encoder->high - encoder->low + 1;
    uint64_t lower = (range * *probability) >> 12;
    uint32_t split;
    int status;
    if (lower == 0 || lower >= range) return INVALID;
    split = encoder->low + (uint32_t)lower - 1;
    if (bit) encoder->low = split + 1;
    else encoder->high = split;
    update_probability(probability, bit);
    for (;;) {
        if (encoder->high < HALF) {
            status = emit_with_pending(encoder, 0);
            if (status != OK) return status;
        } else if (encoder->low >= HALF) {
            status = emit_with_pending(encoder, 1);
            if (status != OK) return status;
            encoder->low -= HALF;
            encoder->high -= HALF;
        } else if (encoder->low >= QUARTER && encoder->high < THREE_QUARTERS) {
            ++encoder->pending;
            encoder->low -= QUARTER;
            encoder->high -= QUARTER;
        } else break;
        encoder->low <<= 1;
        encoder->high = (encoder->high << 1) | 1;
    }
    return OK;
}

static int finish_encoder(Encoder *encoder) {
    unsigned i;
    int status;
    ++encoder->pending;
    status = emit_with_pending(encoder, encoder->low < QUARTER ? 0 : 1);
    if (status != OK) return status;
    for (i = 0; i < 30; ++i) {
        status = put_bit(encoder, 0);
        if (status != OK) return status;
    }
    return OK;
}

static int get_bit(Decoder *decoder, unsigned *bit) {
    if (decoder->used >= decoder->bits) return INVALID;
    *bit = (decoder->data[(size_t)(decoder->used >> 3)] >>
            (7 - (unsigned)(decoder->used & 7))) & 1;
    ++decoder->used;
    return OK;
}

static int decode_bit(Decoder *decoder, uint16_t *probability, unsigned *bit) {
    uint64_t range = (uint64_t)decoder->high - decoder->low + 1;
    uint64_t lower = (range * *probability) >> 12;
    uint32_t split;
    unsigned next;
    if (lower == 0 || lower >= range || decoder->code < decoder->low ||
            decoder->code > decoder->high) return INVALID;
    split = decoder->low + (uint32_t)lower - 1;
    *bit = decoder->code > split;
    if (*bit) decoder->low = split + 1;
    else decoder->high = split;
    update_probability(probability, *bit);
    for (;;) {
        if (decoder->high < HALF) {
            /* No subtraction in the lower half. */
        } else if (decoder->low >= HALF) {
            decoder->low -= HALF;
            decoder->high -= HALF;
            decoder->code -= HALF;
        } else if (decoder->low >= QUARTER && decoder->high < THREE_QUARTERS) {
            decoder->low -= QUARTER;
            decoder->high -= QUARTER;
            decoder->code -= QUARTER;
        } else break;
        decoder->low <<= 1;
        decoder->high = (decoder->high << 1) | 1;
        if (get_bit(decoder, &next) != OK) return INVALID;
        decoder->code = (decoder->code << 1) | next;
    }
    return OK;
}

static int encode_positive(Encoder *encoder, IntegerModel *model, uint32_t value) {
    unsigned k = 0, i;
    uint32_t copy = value;
    int status;
    while (copy >>= 1) ++k;
    if (value == 0 || k >= 24) return INVALID;
    for (i = 0; i < k; ++i) {
        status = encode_bit(encoder, &model->prefix[i], 1);
        if (status != OK) return status;
    }
    status = encode_bit(encoder, &model->prefix[k], 0);
    if (status != OK) return status;
    for (i = k; i != 0; --i) {
        status = encode_bit(encoder, &model->suffix[k][i - 1], (value >> (i - 1)) & 1);
        if (status != OK) return status;
    }
    return OK;
}

static int decode_positive(Decoder *decoder, IntegerModel *model, uint32_t *value) {
    unsigned k, i, bit;
    for (k = 0; k < 24; ++k) {
        if (decode_bit(decoder, &model->prefix[k], &bit) != OK) return INVALID;
        if (!bit) break;
    }
    if (k == 24) return INVALID;
    *value = UINT32_C(1) << k;
    for (i = k; i != 0; --i) {
        if (decode_bit(decoder, &model->suffix[k][i - 1], &bit) != OK) return INVALID;
        *value |= (uint32_t)bit << (i - 1);
    }
    return OK;
}

static uint32_t hash4(const uint8_t *data) {
    return (read32(data) * UINT32_C(2654435761)) >> 13;
}

static uint32_t match_length(const uint8_t *source, uint32_t position,
                             uint32_t candidate, uint32_t limit) {
    uint32_t length;
    if (read32(source + position) != read32(source + candidate)) return 0;
    for (length = 4; length < limit && source[position + length] == source[candidate + length]; ++length) {}
    return length;
}

AHRC_API uint32_t ahrc_version(void) { return 1; }

AHRC_API int ahrc_compress(const uint8_t *source, size_t size,
                          uint8_t *output, size_t capacity, size_t *written) {
    Models *models = NULL;
    uint32_t *heads = NULL, *chain = NULL;
    uint32_t position = 0, last_distance = 1;
    unsigned previous_match = 0, previous_byte = 0;
    Encoder encoder;
    int status = OK;
    if (written == NULL) return INVALID;
    *written = 0;
    if (size > SOURCE_CAP || !valid_buffer(source, size) ||
            !valid_buffer(output, capacity) || overlaps(source, size, output, capacity)) return INVALID;
    if (capacity < 12) return CAPACITY;
    models = new_models();
    if (models == NULL) return ALLOCATION;
    if (size >= 4) {
        heads = (uint32_t *)malloc((size_t)HASH_SIZE * sizeof(*heads));
        chain = (uint32_t *)malloc((size_t)WINDOW * sizeof(*chain));
        if (heads == NULL || chain == NULL) { status = ALLOCATION; goto done; }
        memset(heads, 0xff, (size_t)HASH_SIZE * sizeof(*heads));
    }
    encoder.data = output + 8;
    encoder.capacity = capacity - 8;
    encoder.bits = encoder.pending = 0;
    encoder.low = 0;
    encoder.high = UINT32_MAX;
    while ((size_t)position < size) {
        uint32_t length = 0, distance = 0, consume = 1;
        uint32_t remaining = (uint32_t)(size - position);
        unsigned token_context = (position & 3) * 2 + previous_match;
        if (remaining >= 4) {
            uint32_t limit = remaining < MAX_MATCH ? remaining : MAX_MATCH;
            uint32_t candidate, probes;
            if (last_distance <= position && last_distance <= WINDOW) {
                length = match_length(source, position, position - last_distance, limit);
                if (length >= 4) distance = last_distance;
            }
            candidate = heads[hash4(source + position)];
            for (probes = 0; probes < 32 && length != limit && candidate != NO_POS; ++probes) {
                uint32_t found, next;
                if (candidate >= position || position - candidate > WINDOW) break;
                if (position - candidate != last_distance) {
                    found = match_length(source, position, candidate, limit);
                    if (found > length) { length = found; distance = position - candidate; }
                }
                /* Window/ordering checks precede ring dereference. A live
                 * candidate's slot cannot have been overwritten yet. */
                next = chain[candidate & WINDOW_MASK];
                if (next != NO_POS && next >= candidate) break;
                candidate = next;
            }
        }
        status = encode_bit(&encoder, &models->token[token_context], length >= 4);
        if (status != OK) goto done;
        if (length >= 4) {
            unsigned repeat = distance == last_distance;
            status = encode_bit(&encoder, &models->repeat[position & 3], repeat);
            if (status != OK) goto done;
            if (!repeat) {
                status = encode_positive(&encoder, &models->distance, distance);
                if (status != OK) goto done;
                last_distance = distance;
            }
            status = encode_positive(&encoder, &models->length, length - 3);
            if (status != OK) goto done;
            consume = length;
            previous_match = 1;
        } else {
            unsigned node = 1, i;
            uint8_t value = source[position];
            for (i = 8; i != 0; --i) {
                unsigned bit = (value >> (i - 1)) & 1;
                status = encode_bit(&encoder, &models->literal[previous_byte][node], bit);
                if (status != OK) goto done;
                node = (node << 1) | bit;
            }
            previous_match = 0;
        }
        previous_byte = source[position + consume - 1];
        if (heads != NULL) {
            uint32_t end = position + consume;
            while (position < end) {
                if ((size_t)position + 4 <= size) {
                    uint32_t hash = hash4(source + position);
                    chain[position & WINDOW_MASK] = heads[hash];
                    heads[hash] = position;
                } else chain[position & WINDOW_MASK] = NO_POS;
                ++position;
            }
        } else position += consume;
    }
    status = finish_encoder(&encoder);
    if (status == OK) {
        write64(output, encoder.bits);
        *written = 8 + (size_t)((encoder.bits + 7) >> 3);
    }
done:
    free(chain);
    free(heads);
    free(models);
    return status;
}

AHRC_API int ahrc_decompress(const uint8_t *source, size_t size,
                            uint8_t *output, size_t expected) {
    Decoder decoder;
    Models *models = NULL;
    uint64_t bits, byte_count, i;
    size_t position = 0;
    uint32_t last_distance = 1;
    unsigned previous_byte = 0, previous_match = 0, bit;
    int status = INVALID;
    if (expected > SOURCE_CAP || size < 12 || !valid_buffer(source, size) ||
            !valid_buffer(output, expected) || overlaps(source, size, output, expected)) return INVALID;
    bits = read64(source);
    if (bits < 32 || bits > UINT64_MAX - 7) return INVALID;
    byte_count = (bits + 7) >> 3;
    if (byte_count != size - 8) return INVALID;
    if ((bits & 7) && (source[size - 1] & ((1u << (8 - (unsigned)(bits & 7))) - 1u))) return INVALID;
    for (i = bits - 30; i < bits; ++i)
        if ((source[8 + (size_t)(i >> 3)] >> (7 - (unsigned)(i & 7))) & 1) return INVALID;
    decoder.data = source + 8;
    decoder.bits = bits;
    decoder.used = 0;
    decoder.low = decoder.code = 0;
    decoder.high = UINT32_MAX;
    for (i = 0; i < 32; ++i) {
        if (get_bit(&decoder, &bit) != OK) return INVALID;
        decoder.code = (decoder.code << 1) | bit;
    }
    models = new_models();
    if (models == NULL) return ALLOCATION;
    while (position < expected) {
        unsigned context = ((unsigned)position & 3) * 2 + previous_match;
        if (decode_bit(&decoder, &models->token[context], &bit) != OK) goto done;
        if (!bit) {
            unsigned node = 1, value = 0, j;
            for (j = 0; j < 8; ++j) {
                if (decode_bit(&decoder, &models->literal[previous_byte][node], &bit) != OK) goto done;
                value = (value << 1) | bit;
                node = (node << 1) | bit;
            }
            output[position++] = (uint8_t)value;
            previous_match = 0;
        } else {
            uint32_t distance, length;
            if (decode_bit(&decoder, &models->repeat[position & 3], &bit) != OK) goto done;
            if (bit) distance = last_distance;
            else {
                if (decode_positive(&decoder, &models->distance, &distance) != OK) goto done;
                last_distance = distance;
            }
            if (distance == 0 || distance > WINDOW || distance > position) goto done;
            if (decode_positive(&decoder, &models->length, &length) != OK) goto done;
            if (length == 0 || length > MAX_MATCH - 3) goto done;
            length += 3;
            if (length > expected - position) goto done;
            for (i = 0; i < length; ++i) {
                output[position] = output[position - distance];
                ++position;
            }
            previous_match = 1;
        }
        previous_byte = output[position - 1];
    }
    if (decoder.used == decoder.bits) status = OK;
done:
    free(models);
    return status;
}

AHRC_API int ahrc_word_delta(const uint8_t *source, size_t size,
                            uint8_t *output, int inverse) {
    uint32_t previous[3] = {0, 0, 0};
    size_t position;
    if (size == 0 || size > TABLE_CAP || size % 12 || (inverse != 0 && inverse != 1) ||
            !valid_buffer(source, size) || !valid_buffer(output, size) ||
            (source != output && overlaps(source, size, output, size))) return INVALID;
    for (position = 0; position < size; position += 12) {
        unsigned field;
        for (field = 0; field < 3; ++field) {
            uint32_t value = read32(source + position + 4 * field);
            uint32_t result = inverse ? value + previous[field] : value - previous[field];
            write32(output + position + 4 * field, result);
            previous[field] = inverse ? result : value;
        }
    }
    return OK;
}
