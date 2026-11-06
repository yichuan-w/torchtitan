#ifndef PARSER_H
#define PARSER_H

#include <stdint.h>
#include <stdio.h>

/* Opaque parser context */
typedef struct {
    FILE *fp;
    uint32_t num_items;
    uint16_t flags;
    uint32_t current_item;
} parser_ctx_t;

/* Parsed item data */
typedef struct {
    uint16_t item_id;
    uint16_t data_len;
    uint8_t *data;
} parsed_item_t;

/* Create a parser for the given file. Returns NULL on error. */
parser_ctx_t *parser_create(const char *filename);

/* Read the next item. Returns 1 on success, 0 if no more items, -1 on error. */
int parser_next_item(parser_ctx_t *ctx, parsed_item_t *item);

/* Read the checksum from the file. */
uint32_t parser_read_checksum(parser_ctx_t *ctx);

/* Free the parser context. */
void parser_destroy(parser_ctx_t *ctx);

/* Free the data inside a parsed item. */
void parser_free_item(parsed_item_t *item);

#endif
