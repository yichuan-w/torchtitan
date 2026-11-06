#ifndef ASM_H
#define ASM_H

#include <stdint.h>
#include <stddef.h>

#define ASM_MAX_CODE 65536
#define ASM_MAX_LABELS 1024

typedef struct {
    char name[64];
    uint32_t offset;       // byte offset in the code buffer
    int defined;           // 1 if the label position is known
} Label;

typedef struct {
    // Pending fixup: instruction at code_offset needs label's value patched
    uint32_t code_offset;  // where in code the offset needs to be written
    char label_name[64];   // which label to resolve
} Fixup;

typedef struct {
    uint8_t code[ASM_MAX_CODE];
    uint32_t code_len;

    Label labels[ASM_MAX_LABELS];
    int label_count;

    Fixup fixups[ASM_MAX_LABELS];
    int fixup_count;
} Assembler;

void asm_init(Assembler *a);
int  asm_assemble(Assembler *a, const char *source);
const uint8_t *asm_get_code(const Assembler *a);
uint32_t asm_get_code_len(const Assembler *a);

#endif