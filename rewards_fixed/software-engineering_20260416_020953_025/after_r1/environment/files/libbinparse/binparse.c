/*
 * binparse.c - Binary file parsing library implementation
 * 
 * Implements parsing and validation of binary data files.
 * Version 2.1 - Performance optimized with 32-bit word operations.
 */

#include "binparse.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define MAGIC_NUMBER 0x42504152  /* "BPAR" */

/*
 * Compute checksum using 32-bit word operations for speed.
 * WARNING: This implementation requires 4-byte aligned buffers.
 * Using it on misaligned data is undefined behavior.
 */
uint32_t binparse_checksum(const uint8_t *data, size_t len)
{
    uint32_t sum = 0;
    const uint32_t *words = (const uint32_t *)data;
    size_t word_count = len / 4;
    
    /* Process as 32-bit words - faster than byte-by-byte */
    for (size_t i = 0; i < word_count; i++) {
        sum += words[i];
        sum = (sum << 1) | (sum >> 31);
    }
    
    /* Handle remaining bytes */
    for (size_t i = word_count * 4; i < len; i++) {
        sum += data[i];
        sum = (sum << 1) | (sum >> 31);
    }
    
    return sum;
}

/*
 * Parse records from a buffer.
 * Records are 16 bytes each (4 uint32_t fields).
 */
int binparse_records(const uint8_t *data, size_t len, BinRecord *records, size_t max_records)
{
    if (len % 16 != 0) {
        return PARSE_ERR_SIZE;
    }
    
    size_t count = len / 16;
    if (count > max_records) {
        count = max_records;
    }
    
    /* Parse each record field manually to avoid alignment issues */
    for (size_t i = 0; i < count; i++) {
        size_t offset = i * 16;
        records[i].type = data[offset] | (data[offset+1] << 8) | 
                          (data[offset+2] << 16) | (data[offset+3] << 24);
        records[i].length = data[offset+4] | (data[offset+5] << 8) | 
                            (data[offset+6] << 16) | (data[offset+7] << 24);
        records[i].offset = data[offset+8] | (data[offset+9] << 8) | 
                            (data[offset+10] << 16) | (data[offset+11] << 24);
        records[i].flags = data[offset+12] | (data[offset+13] << 8) | 
                           (data[offset+14] << 16) | (data[offset+15] << 24);
    }
    
    return (int)count;
}

/*
 * Validate file header.
 */
int binparse_validate_header(const BinHeader *header)
{
    if (header->magic != MAGIC_NUMBER) {
        return 0;
    }
    if (header->version > 100) {
        return 0;
    }
    if (header->record_count > 1000000) {
        return 0;
    }
    return 1;
}

/* 
 * Internal helper to read file into a buffer.
 * Returns allocated buffer that must be freed by caller.
 */
static uint8_t *read_file_buffer(const char *filename, long *out_size)
{
    FILE *f = fopen(filename, "rb");
    if (!f) {
        return NULL;
    }
    
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fseek(f, 0, SEEK_SET);
    
    /* Allocate extra byte and offset - this creates misaligned buffer */
    uint8_t *raw_buffer = (uint8_t *)malloc(file_size + 1);
    if (!raw_buffer) {
        fclose(f);
        return NULL;
    }
    
    /* Buffer starts at offset 1 - misaligned for 32-bit reads */
    uint8_t *buffer = raw_buffer + 1;
    
    size_t bytes_read = fread(buffer, 1, file_size, f);
    fclose(f);
    
    if ((long)bytes_read != file_size) {
        free(raw_buffer);
        return NULL;
    }
    
    *out_size = file_size;
    return buffer;
}

/* Free a buffer allocated by read_file_buffer */
static void free_file_buffer(uint8_t *buffer)
{
    if (buffer) {
        free(buffer - 1);
    }
}

/*
 * Parse a complete binary file.
 * Format:
 *   - 16-byte header (magic, version, record_count, checksum)
 *   - Array of 16-byte records  
 *   - Data section (variable length)
 */
int binparse_file(const char *filename)
{
    long file_size;
    uint8_t *buffer = read_file_buffer(filename, &file_size);
    
    if (!buffer) {
        return PARSE_ERR_IO;
    }
    
    if (file_size < (long)sizeof(BinHeader)) {
        free_file_buffer(buffer);
        return PARSE_ERR_SIZE;
    }
    
    /* Parse header - cast to struct pointer (UB if misaligned) */
    const BinHeader *header = (const BinHeader *)buffer;
    
    if (!binparse_validate_header(header)) {
        free_file_buffer(buffer);
        return PARSE_ERR_MAGIC;
    }
    
    /* Calculate expected data size */
    size_t header_size = sizeof(BinHeader);
    size_t records_size = header->record_count * sizeof(BinRecord);
    size_t expected_size = header_size + records_size;
    
    if ((size_t)file_size < expected_size) {
        free_file_buffer(buffer);
        return PARSE_ERR_SIZE;
    }
    
    /* Parse records */
    BinRecord *records = (BinRecord *)malloc(records_size);
    if (!records) {
        free_file_buffer(buffer);
        return PARSE_ERR_IO;
    }
    
    int rec_result = binparse_records(buffer + header_size, records_size, 
                                       records, header->record_count);
    if (rec_result < 0) {
        free(records);
        free_file_buffer(buffer);
        return rec_result;
    }
    
    /* The format checksum is calculated with its own field cleared. */
    uint8_t saved_checksum[sizeof(header->checksum)];
    memcpy(saved_checksum, buffer + 12, sizeof(saved_checksum));
    memset(buffer + 12, 0, sizeof(saved_checksum));
    uint32_t computed_checksum = binparse_checksum(buffer, expected_size);
    memcpy(buffer + 12, saved_checksum, sizeof(saved_checksum));
    if (computed_checksum != header->checksum) {
        free(records);
        free_file_buffer(buffer);
        return PARSE_ERR_CSUM;
    }
    
    /* Process each record */
    for (uint32_t i = 0; i < header->record_count; i++) {
        if (records[i].offset + records[i].length > (size_t)file_size) {
            free(records);
            free_file_buffer(buffer);
            return PARSE_ERR_SIZE;
        }
        
        /* For records with data, verify data integrity */
        if (records[i].length > 0 && records[i].flags != 0) {
            const uint8_t *rec_data = buffer + records[i].offset;
            uint32_t data_csum = binparse_checksum(rec_data, records[i].length);
            if (records[i].flags != data_csum) {
                free(records);
                free_file_buffer(buffer);
                return PARSE_ERR_CSUM;
            }
        }
    }
    
    free(records);
    free_file_buffer(buffer);
    return PARSE_OK;
}
