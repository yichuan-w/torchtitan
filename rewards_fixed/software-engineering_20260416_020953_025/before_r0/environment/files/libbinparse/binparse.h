/*
 * binparse.h - Binary file parsing library header
 * 
 * A simple library for parsing binary data files with
 * checksum validation and record extraction.
 */

#ifndef BINPARSE_H
#define BINPARSE_H

#include <stdint.h>
#include <stddef.h>

/* File header structure */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t record_count;
    uint32_t checksum;
} BinHeader;

/* Record structure */
typedef struct {
    uint32_t type;
    uint32_t length;
    uint32_t offset;
    uint32_t flags;
} BinRecord;

/* Parse result codes */
#define PARSE_OK         0
#define PARSE_ERR_IO    -1
#define PARSE_ERR_MAGIC -2
#define PARSE_ERR_CSUM  -3
#define PARSE_ERR_SIZE  -4

/*
 * Parse a binary file and validate its structure.
 * Returns PARSE_OK on success, error code on failure.
 */
int binparse_file(const char *filename);

/*
 * Compute checksum over a buffer of bytes.
 * Uses a rolling 32-bit algorithm.
 */
uint32_t binparse_checksum(const uint8_t *data, size_t len);

/*
 * Parse records from a buffer.
 * Returns number of records parsed, or negative on error.
 */
int binparse_records(const uint8_t *data, size_t len, BinRecord *records, size_t max_records);

/*
 * Validate file header.
 * Returns 1 if valid, 0 if invalid.
 */
int binparse_validate_header(const BinHeader *header);

#endif /* BINPARSE_H */
