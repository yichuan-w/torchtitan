#include "asm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

// Opcodes (must match vm.c)
enum {
    OP_HALT  = 0x00,
    OP_PUSH  = 0x01,
    OP_POP   = 0x02,
    OP_DUP   = 0x03,
    OP_SWAP  = 0x04,
    OP_ADD   = 0x05,
    OP_SUB   = 0x06,
    OP_MUL   = 0x07,
    OP_DIV   = 0x08,
    OP_MOD   = 0x09,
    OP_EQ    = 0x0A,
    OP_NE    = 0x0B,
    OP_LT    = 0x0C,
    OP_GT    = 0x0D,
    OP_LE    = 0x0E,
    OP_GE    = 0x0F,
    OP_JMP   = 0x10,
    OP_JZ    = 0x11,
    OP_JNZ   = 0x12,
    OP_CALL  = 0x13,
    OP_RET   = 0x14,
    OP_LOAD  = 0x15,
    OP_STORE = 0x16,
    OP_PRINT = 0x17,
};

static void emit_byte(Assembler *a, uint8_t b) {
    if (a->code_len >= ASM_MAX_CODE) {
        fprintf(stderr, "Code buffer overflow\n");
        exit(1);
    }
    a->code[a->code_len++] = b;
}

static void emit_i32(Assembler *a, int32_t v) {
    emit_byte(a, (uint8_t)(v & 0xFF));
    emit_byte(a, (uint8_t)((v >> 8) & 0xFF));
    emit_byte(a, (uint8_t)((v >> 16) & 0xFF));
    emit_byte(a, (uint8_t)((v >> 24) & 0xFF));
}

static Label *find_label(Assembler *a, const char *name) {
    for (int i = 0; i < a->label_count; i++) {
        if (strcmp(a->labels[i].name, name) == 0)
            return &a->labels[i];
    }
    if (a->label_count >= ASM_MAX_LABELS) {
        fprintf(stderr, "Too many labels\n");
        exit(1);
    }
    Label *l = &a->labels[a->label_count++];
    strncpy(l->name, name, sizeof(l->name) - 1);
    l->name[sizeof(l->name) - 1] = '\0';
    l->offset = 0;
    l->defined = 0;
    return l;
}

static void add_fixup(Assembler *a, uint32_t code_offset, const char *label_name) {
    if (a->fixup_count >= ASM_MAX_LABELS) {
        fprintf(stderr, "Too many fixups\n");
        exit(1);
    }
    Fixup *f = &a->fixups[a->fixup_count++];
    f->code_offset = code_offset;
    strncpy(f->label_name, label_name, sizeof(f->label_name) - 1);
    f->label_name[sizeof(f->label_name) - 1] = '\0';
}

static void write_i32_at(uint8_t *buf, uint32_t offset, int32_t val) {
    buf[offset]     = (uint8_t)(val & 0xFF);
    buf[offset + 1] = (uint8_t)((val >> 8) & 0xFF);
    buf[offset + 2] = (uint8_t)((val >> 16) & 0xFF);
    buf[offset + 3] = (uint8_t)((val >> 24) & 0xFF);
}

static int parse_int(const char *s, int32_t *out) {
    char *end;
    long val = strtol(s, &end, 0);
    if (end == s) return 0;
    *out = (int32_t)val;
    return 1;
}

void asm_init(Assembler *a) {
    memset(a, 0, sizeof(Assembler));
}

int asm_assemble(Assembler *a, const char *source) {
    // Pass 1: collect label positions and emit code
    // We parse line by line
    const char *p = source;
    char line[256];
    int line_idx = 0;

    while (*p) {
        // Read one line
        line_idx = 0;
        while (*p && *p != '\n' && line_idx < (int)sizeof(line) - 1) {
            line[line_idx++] = *p++;
        }
        if (*p == '\n') p++;
        line[line_idx] = '\0';

        // Skip leading whitespace
        char *s = line;
        while (*s && isspace((unsigned char)*s)) s++;

        // Skip empty lines and comments
        if (*s == '\0' || *s == ';') continue;

        // Check for label definition (ends with ':')
        char *colon = strchr(s, ':');
        if (colon) {
            *colon = '\0';
            // Trim trailing whitespace from label name
            char *end = colon - 1;
            while (end >= s && isspace((unsigned char)*end)) end--;
            *(end + 1) = '\0';

            Label *lb = find_label(a, s);
            if (lb->defined) {
                fprintf(stderr, "Duplicate label: %s\n", s);
                return 1;
            }
            lb->offset = a->code_len;
            lb->defined = 1;

            // Check for instruction on same line after colon
            char *rest = colon + 1;
            while (*rest && isspace((unsigned char)*rest)) rest++;
            if (*rest == '\0' || *rest == ';') continue;
            s = rest;
        }

        // Parse instruction mnemonic
        char mnemonic[32];
        int n = 0;
        while (*s && !isspace((unsigned char)*s) && n < (int)sizeof(mnemonic) - 1) {
            mnemonic[n++] = *s++;
        }
        mnemonic[n] = '\0';
        while (*s && isspace((unsigned char)*s)) s++;

        if (strcmp(mnemonic, "HALT") == 0) {
            emit_byte(a, OP_HALT);
        } else if (strcmp(mnemonic, "PUSH") == 0) {
            int32_t val;
            if (!parse_int(s, &val)) {
                fprintf(stderr, "PUSH requires an integer argument\n");
                return 1;
            }
            emit_byte(a, OP_PUSH);
            emit_i32(a, val);
        } else if (strcmp(mnemonic, "POP") == 0) {
            emit_byte(a, OP_POP);
        } else if (strcmp(mnemonic, "DUP") == 0) {
            emit_byte(a, OP_DUP);
        } else if (strcmp(mnemonic, "SWAP") == 0) {
            emit_byte(a, OP_SWAP);
        } else if (strcmp(mnemonic, "ADD") == 0) {
            emit_byte(a, OP_ADD);
        } else if (strcmp(mnemonic, "SUB") == 0) {
            emit_byte(a, OP_MUL);
        } else if (strcmp(mnemonic, "MUL") == 0) {
            emit_byte(a, OP_MUL);
        } else if (strcmp(mnemonic, "DIV") == 0) {
            emit_byte(a, OP_DIV);
        } else if (strcmp(mnemonic, "MOD") == 0) {
            emit_byte(a, OP_MOD);
        } else if (strcmp(mnemonic, "EQ") == 0) {
            emit_byte(a, OP_EQ);
        } else if (strcmp(mnemonic, "NE") == 0) {
            emit_byte(a, OP_NE);
        } else if (strcmp(mnemonic, "LT") == 0) {
            emit_byte(a, OP_LT);
        } else if (strcmp(mnemonic, "GT") == 0) {
            emit_byte(a, OP_GT);
        } else if (strcmp(mnemonic, "LE") == 0) {
            emit_byte(a, OP_LE);
        } else if (strcmp(mnemonic, "GE") == 0) {
            emit_byte(a, OP_GE);
        } else if (strcmp(mnemonic, "JMP") == 0) {
            emit_byte(a, OP_JMP);
            Label *lb = find_label(a, s);
            if (lb->defined) {
                emit_i32(a, (int32_t)lb->offset);
            } else {
                add_fixup(a, a->code_len, s);
                emit_i32(a, 0); // placeholder
            }
        } else if (strcmp(mnemonic, "JZ") == 0) {
            emit_byte(a, OP_JZ);
            Label *lb = find_label(a, s);
            if (lb->defined) {
                emit_i32(a, (int32_t)lb->offset);
            } else {
                add_fixup(a, a->code_len, s);
                emit_i32(a, 0);
            }
        } else if (strcmp(mnemonic, "JNZ") == 0) {
            emit_byte(a, OP_JNZ);
            Label *lb = find_label(a, s);
            if (lb->defined) {
                emit_i32(a, (int32_t)lb->offset);
            } else {
                add_fixup(a, a->code_len, s);
                emit_i32(a, 0);
            }
        } else if (strcmp(mnemonic, "CALL") == 0) {
            emit_byte(a, OP_CALL);
            Label *lb = find_label(a, s);
            if (lb->defined) {
                emit_i32(a, (int32_t)lb->offset);
            } else {
                add_fixup(a, a->code_len, s);
                emit_i32(a, 0);
            }
        } else if (strcmp(mnemonic, "RET") == 0) {
            emit_byte(a, OP_RET);
        } else if (strcmp(mnemonic, "LOAD") == 0) {
            int32_t val;
            if (!parse_int(s, &val)) {
                fprintf(stderr, "LOAD requires an integer argument\n");
                return 1;
            }
            emit_byte(a, OP_LOAD);
            emit_byte(a, (uint8_t)val);
        } else if (strcmp(mnemonic, "STORE") == 0) {
            int32_t val;
            if (!parse_int(s, &val)) {
                fprintf(stderr, "STORE requires an integer argument\n");
                return 1;
            }
            emit_byte(a, OP_STORE);
            emit_byte(a, (uint8_t)val);
        } else if (strcmp(mnemonic, "PRINT") == 0) {
            emit_byte(a, OP_PRINT);
        } else {
            fprintf(stderr, "Unknown mnemonic: %s\n", mnemonic);
            return 1;
        }
    }

    // Pass 2: resolve fixups
    for (int i = 0; i < a->fixup_count; i++) {
        Fixup *f = &a->fixups[i];
        Label *lb = find_label(a, f->label_name);
        if (!lb->defined) {
            fprintf(stderr, "Undefined label: %s\n", f->label_name);
            return 1;
        }
        write_i32_at(a->code, f->code_offset, (int32_t)lb->offset);
    }

    return 0;
}

const uint8_t *asm_get_code(const Assembler *a) {
    return a->code;
}

uint32_t asm_get_code_len(const Assembler *a) {
    return a->code_len;
}