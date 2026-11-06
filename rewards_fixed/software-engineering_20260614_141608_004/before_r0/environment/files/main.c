#include "vm.h"
#include "asm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "Cannot open file: %s\n", path);
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(len + 1);
    if (!buf) {
        fclose(f);
        return NULL;
    }
    size_t n = fread(buf, 1, len, f);
    buf[n] = '\0';
    fclose(f);
    *out_len = n;
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file.asm>\n", argv[0]);
        return 1;
    }

    size_t src_len;
    char *source = read_file(argv[1], &src_len);
    if (!source) return 1;

    Assembler asm_;
    asm_init(&asm_);
    if (asm_assemble(&asm_, source) != 0) {
        free(source);
        return 1;
    }
    free(source);

    VM vm;
    vm_init(&vm);
    vm_load(&vm, asm_get_code(&asm_), asm_get_code_len(&asm_));
    return vm_run(&vm);
}