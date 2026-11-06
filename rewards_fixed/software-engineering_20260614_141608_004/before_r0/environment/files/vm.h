#ifndef VM_H
#define VM_H

#include <stdint.h>
#include <stdbool.h>

#define MAX_STACK 1024
#define MAX_LOCALS 256
#define MAX_CALLSTACK 256
#define MAX_CODE 65536

// Execution trace flags (for future debugger support)
#define TRACE_NONE     0x00
#define TRACE_OPS      0x01
#define TRACE_STACK    0x02
#define TRACE_LOCALS   0x04
#define TRACE_JUMPS    0x08
#define TRACE_CALLS    0x10

// Statistics counters
typedef struct {
    uint32_t instructions_executed;
    uint32_t pushes;
    uint32_t pops;
    uint32_t jumps_taken;
    uint32_t jumps_skipped;
    uint32_t calls_made;
    uint32_t returns;
    uint32_t arithmetic_ops;
    uint32_t comparison_ops;
    uint32_t stack_overflow_checks;
    uint32_t stack_underflow_checks;
} VMStats;

typedef struct {
    int32_t stack[MAX_STACK];
    int32_t sp;              // stack pointer (next free slot)

    int32_t locals[MAX_LOCALS];

    uint32_t call_stack[MAX_CALLSTACK];
    int32_t cp;              // call stack pointer (next free slot)

    uint8_t code[MAX_CODE];
    uint32_t code_len;
    uint32_t pc;             // program counter

    bool running;

    // Debugging / statistics support (currently unused in production)
    uint8_t trace_flags;
    VMStats stats;
} VM;

void vm_init(VM *vm);
void vm_load(VM *vm, const uint8_t *code, uint32_t len);
int  vm_run(VM *vm);

// Statistics helpers (unused, for future use)
void vm_stats_reset(VM *vm);
void vm_stats_print(const VM *vm);
void vm_set_trace(VM *vm, uint8_t flags);

#endif