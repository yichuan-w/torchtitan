#include "parser.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>

/* Read a big-endian uint32_t */
static uint32_t read_u32(FILE *fp) {
    uint32_t val;
    if (fread(&val, 1, 4, fp) != 4) {
        fprintf(stderr, "Error reading uint32\n");
        exit(1);
    }
    return ntohl(val);
}

/* Read a big-endian uint16_t */
static uint16_t read_u16(FILE *fp) {
    uint16_t val;
    if (fread(&val, 1, 2, fp) != 2) {
        fprintf(stderr, "Error reading uint16\n");
        exit(1);
    }
    return ntohs(val);
}

parser_ctx_t *parser_create(const char *filename) {
    FILE *fp = fopen(filename, "rb");
    if (!fp) {
        perror("fopen");
        return NULL;
    }
    
    char magic[4];
    if (fread(magic, 1, 4, fp) != 4 || memcmp(magic, "DTA\x01", 4) != 0) {
        fprintf(stderr, "Invalid magic number\n");
        fclose(fp);
        return NULL;
    }
    
    parser_ctx_t *ctx = malloc(sizeof(parser_ctx_t));
    if (!ctx) { fclose(fp); return NULL; }
    
    ctx->fp = fp;
    ctx->num_items = read_u32(fp);
    ctx->flags = read_u16(fp);
    ctx->current_item = 0;
    
    return ctx;
}

int parser_next_item(parser_ctx_t *ctx, parsed_item_t *item) {
    if (ctx->current_item >= ctx->num_items) {
        return 0;  /* No more items */
    }
    
    item->item_id = read_u16(ctx->fp);
    item->data_len = read_u16(ctx->fp);
    
    if (item->data_len > 0) {
        /* BUG: alloc_size computed as uint16_t wraps when data_len = 0xFFFF */
        uint16_t alloc_size = item->data_len + 1;
        item->data = malloc(alloc_size);
        if (!item->data) {
            fprintf(stderr, "Failed to allocate data for item %u\n", ctx->current_item);
            return -1;
        }
        
        if (fread(item->data, 1, item->data_len, ctx->fp) != item->data_len) {
            fprintf(stderr, "Error reading data for item %u\n", ctx->current_item);
            free(item->data);
            item->data = NULL;
            return -1;
        }
        
        item->data[item->data_len] = '\0';
    } else {
        item->data = NULL;
    }
    
    ctx->current_item++;
    return 1;
}

uint32_t parser_read_checksum(parser_ctx_t *ctx) {
    return read_u32(ctx->fp);
}

void parser_destroy(parser_ctx_t *ctx) {
    if (ctx) {
        if (ctx->fp) fclose(ctx->fp);
        free(ctx);
    }
}

void parser_free_item(parsed_item_t *item) {
    if (item && item->data) {
        free(item->data);
        item->data = NULL;
    }
}
