#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <arpa/inet.h>

/* BLOG binary log format reader */

typedef struct {
    char magic[4];
    uint16_t version;
    uint16_t flags;
    uint32_t entry_count;
    uint32_t reserved;
} __attribute__((packed)) blog_header;

typedef struct {
    uint32_t timestamp;   /* microseconds since epoch */
    uint8_t  level;
    uint16_t source_len;
    /* followed by: char source[source_len] */
    /* followed by: uint16_t msg_len */
    /* followed by: char message[msg_len] */
} __attribute__((packed)) blog_entry_hdr;

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <blog_file>\n", argv[0]);
        return 1;
    }

    FILE *fp = fopen(argv[1], "rb");
    if (!fp) {
        perror("fopen");
        return 1;
    }

    /* Read header */
    blog_header hdr;
    if (fread(&hdr, sizeof(hdr), 1, fp) != 1) {
        fprintf(stderr, "Error reading header\n");
        fclose(fp);
        return 1;
    }

    if (memcmp(hdr.magic, "BLOG", 4) != 0) {
        fprintf(stderr, "Invalid magic\n");
        fclose(fp);
        return 1;
    }

    hdr.version = ntohs(hdr.version);
    hdr.flags = ntohs(hdr.flags);
    hdr.entry_count = ntohl(hdr.entry_count);

    printf("[\n");
    int first = 1;

    for (uint32_t i = 0; i < hdr.entry_count; i++) {
        blog_entry_hdr entry;
        if (fread(&entry, sizeof(entry), 1, fp) != 1) {
            break;
        }

        entry.source_len = ntohs(entry.source_len);

        char *source = malloc(entry.source_len + 1);
        if (!source) break;
        if (fread(source, 1, entry.source_len, fp) != entry.source_len) {
            free(source);
            break;
        }
        source[entry.source_len] = '\0';

        uint16_t msg_len;
        if (fread(&msg_len, sizeof(msg_len), 1, fp) != 1) {
            free(source);
            break;
        }
        msg_len = ntohs(msg_len);

        char *message = malloc(msg_len + 1);
        if (!message) { free(source); break; }
        if (fread(message, 1, msg_len, fp) != msg_len) {
            free(source);
            free(message);
            break;
        }
        message[msg_len] = '\0';

        if (!first) printf(",\n");
        first = 0;

        printf("  {\n");
        printf("    \"timestamp\": %u,\n", entry.timestamp);
        printf("    \"level\": %u,\n", entry.level);
        printf("    \"source\": \"%s\",\n", source);
        printf("    \"message\": \"%s\"\n", message);
        printf("  }");

        free(source);
        free(message);
    }

    printf("\n]\n");
    fclose(fp);
    return 0;
}