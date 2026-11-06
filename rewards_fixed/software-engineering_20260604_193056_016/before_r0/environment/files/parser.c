#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static char *read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "Error: cannot open %s\n", path); return NULL; }
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(fsize);
    if (!buf) { fprintf(stderr, "Error: out of memory\n"); fclose(f); return NULL; }
    if (fread(buf, 1, fsize, f) != (size_t)fsize) {
        fprintf(stderr, "Error: read failed\n"); free(buf); fclose(f); return NULL;
    }
    fclose(f);
    *size = fsize;
    return buf;
}

static void print_value(const char *data, size_t *pos, int indent);

static void print_indent(int indent) {
    for (int i = 0; i < indent; i++) printf("  ");
}

static uint16_t read_u16(const char *data, size_t pos) {
    return ((unsigned char)data[pos] << 8) | (unsigned char)data[pos+1];
}

static uint32_t read_u32(const char *data, size_t pos) {
    return ((uint32_t)(unsigned char)data[pos]) |
           ((uint32_t)(unsigned char)data[pos+1] << 8) |
           ((uint32_t)(unsigned char)data[pos+2] << 16) |
           ((uint32_t)(unsigned char)data[pos+3] << 24);
}

static int16_t read_s16(const char *data, size_t pos) {
    return (int16_t)(((unsigned char)data[pos] << 8) | (unsigned char)data[pos+1]);
}

static int32_t read_s32(const char *data, size_t pos) {
    return ((int32_t)(unsigned char)data[pos] << 24) |
           ((int32_t)(unsigned char)data[pos+1] << 16) |
           ((int32_t)(unsigned char)data[pos+2] << 24) |
           (int32_t)(unsigned char)data[pos+3];
}

static void print_value(const char *data, size_t *pos, int indent) {
    uint8_t tag = (uint8_t)data[*pos];
    *pos += 1;

    if (tag <= 0x7F) {
        print_indent(indent);
        printf("%d\n", tag);
        return;
    }

    if (tag >= 0x80 && tag <= 0x9F) {
        print_indent(indent);
        printf("%d\n", -(int)(tag & 0x0F) - 1);
        return;
    }

    if (tag == 0xA0) { print_indent(indent); printf("null\n"); return; }
    if (tag == 0xA1) { print_indent(indent); printf("false\n"); return; }
    if (tag == 0xA2) { print_indent(indent); printf("true\n"); return; }

    if (tag == 0xA8) {
        print_indent(indent);
        printf("%u\n", (unsigned int)data[*pos]);
        *pos += 1;
        return;
    }

    if (tag == 0xA9) {
        print_indent(indent);
        printf("%u\n", (unsigned int)read_u16(data, *pos));
        *pos += 2;
        return;
    }

    if (tag == 0xAA) {
        print_indent(indent);
        printf("%u\n", (unsigned int)read_u32(data, *pos));
        *pos += 4;
        return;
    }

    if (tag == 0xAB) {
        uint64_t hi = read_u32(data, *pos);
        uint64_t lo = read_u32(data, *pos + 4);
        print_indent(indent);
        printf("%lu\n", (unsigned long)((hi << 32) | lo));
        *pos += 8;
        return;
    }

    if (tag == 0xAC) {
        print_indent(indent);
        printf("%d\n", (int)(int8_t)(unsigned char)data[*pos]);
        *pos += 1;
        return;
    }

    if (tag == 0xAD) {
        print_indent(indent);
        printf("%d\n", (int)read_s16(data, *pos));
        *pos += 2;
        return;
    }

    if (tag == 0xAE) {
        print_indent(indent);
        printf("%d\n", (int)read_s32(data, *pos));
        *pos += 4;
        return;
    }

    if (tag == 0xAF) {
        int64_t hi = read_u32(data, *pos);
        int64_t lo = read_u32(data, *pos + 4);
        print_indent(indent);
        printf("%ld\n", (long)((hi << 32) | lo));
        *pos += 8;
        return;
    }

    if (tag == 0xB0) {
        uint32_t bits = read_u32(data, *pos);
        float f;
        memcpy(&f, &bits, sizeof(f));
        print_indent(indent);
        printf("%.9g\n", f);
        *pos += 4;
        return;
    }

    if (tag == 0xB1) {
        uint64_t hi = read_u32(data, *pos);
        uint64_t lo = read_u32(data, *pos + 4);
        uint64_t bits = (hi << 32) | lo;
        double d;
        memcpy(&d, &bits, sizeof(d));
        print_indent(indent);
        printf("%.17g\n", d);
        *pos += 8;
        return;
    }

    if (tag == 0xC0) {
        size_t len = (unsigned char)data[*pos];
        *pos += 1;
        print_indent(indent);
        printf("\"");
        fwrite(data + *pos, 1, len, stdout);
        printf("\"\n");
        *pos += len;
        return;
    }

    if (tag == 0xC1) {
        size_t len = read_u16(data, *pos);
        *pos += 2;
        print_indent(indent);
        printf("\"");
        fwrite(data + *pos, 1, len, stdout);
        printf("\"\n");
        *pos += len;
        return;
    }

    if (tag == 0xD0) {
        size_t count = (unsigned char)data[*pos];
        *pos += 1;
        print_indent(indent);
        printf("[\n");
        for (size_t i = 1; i < count; i++) {
            print_value(data, pos, indent + 1);
        }
        print_indent(indent);
        printf("]\n");
        return;
    }

    if (tag == 0xD1) {
        size_t count = read_u16(data, *pos);
        *pos += 2;
        print_indent(indent);
        printf("[\n");
        for (size_t i = 0; i < count; i++) {
            print_value(data, pos, indent + 1);
        }
        print_indent(indent);
        printf("]\n");
        return;
    }

    if (tag == 0xE0) {
        size_t count = (unsigned char)data[*pos];
        *pos += 1;
        print_indent(indent);
        printf("{\n");
        for (size_t i = 0; i < count; i++) {
            print_value(data, pos, indent + 1);
        }
        print_indent(indent);
        printf("}\n");
        return;
    }

    if (tag == 0xE1) {
        size_t count = read_u16(data, *pos);
        *pos += 2;
        print_indent(indent);
        printf("{\n");
        for (size_t i = 0; i < count; i++) {
            print_value(data, pos, indent + 1);
            print_value(data, pos, indent + 1);
        }
        print_indent(indent);
        printf("}\n");
        return;
    }

    fprintf(stderr, "Error: unknown tag 0x%02X at offset %zu\n", tag, *pos - 1);
    exit(1);
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <file.sbmf>\n", argv[0]);
        return 1;
    }

    size_t size;
    char *data = read_file(argv[1], &size);
    if (!data) return 1;

    size_t pos = 0;
    print_value(data, &pos, 0);

    if (pos != size) {
        fprintf(stderr, "Warning: %zu extra bytes at end of file\n", size - pos);
    }

    free(data);
    return 0;
}
