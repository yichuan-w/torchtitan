#include "vm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Opcodes
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

static int32_t read_i32(const uint8_t *p) {
    return (int32_t)((uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                     ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
}

void vm_init(VM *vm) {
    memset(vm, 0, sizeof(VM));
}

void vm_load(VM *vm, const uint8_t *code, uint32_t len) {
    if (len > MAX_CODE) {
        fprintf(stderr, "Code too large: %u bytes\n", len);
        exit(1);
    }
    memcpy(vm->code, code, len);
    vm->code_len = len;
    vm->pc = 0;
    vm->running = true;
}

// Statistics helpers (unused, reserved for future debugger integration)
void vm_stats_reset(VM *vm) {
    memset(&vm->stats, 0, sizeof(VMStats));
}

void vm_stats_print(const VM *vm) {
    fprintf(stderr, "=== VM Statistics ===\n");
    fprintf(stderr, "Instructions: %u\n", vm->stats.instructions_executed);
    fprintf(stderr, "Pushes: %u\n", vm->stats.pushes);
    fprintf(stderr, "Pops: %u\n", vm->stats.pops);
    fprintf(stderr, "Jumps taken: %u\n", vm->stats.jumps_taken);
    fprintf(stderr, "Jumps skipped: %u\n", vm->stats.jumps_skipped);
    fprintf(stderr, "Calls: %u\n", vm->stats.calls_made);
    fprintf(stderr, "Returns: %u\n", vm->stats.returns);
    fprintf(stderr, "Arithmetic ops: %u\n", vm->stats.arithmetic_ops);
    fprintf(stderr, "Comparisons: %u\n", vm->stats.comparison_ops);
}

void vm_set_trace(VM *vm, uint8_t flags) {
    vm->trace_flags = flags;
}

int vm_run(VM *vm) {
    while (vm->running) {
        if (vm->pc >= vm->code_len) {
            fprintf(stderr, "PC out of bounds: %u\n", vm->pc);
            return 1;
        }

        uint8_t op = vm->code[vm->pc];
        vm->pc++;

        switch (op) {
        case OP_HALT:
            vm->running = false;
            break;

        case OP_PUSH: {
            if (vm->pc + 4 > vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            int32_t val = read_i32(&vm->code[vm->pc]);
            vm->pc += 4;
            if (vm->sp >= MAX_STACK) { fprintf(stderr, "Stack overflow\n"); return 1; }
            vm->stack[vm->sp++] = val;
            break;
        }

        case OP_POP: {
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            vm->sp--;
            break;
        }

        case OP_DUP: {
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            if (vm->sp >= MAX_STACK) { fprintf(stderr, "Stack overflow\n"); return 1; }
            vm->stack[vm->sp] = vm->stack[vm->sp - 1];
            vm->sp++;
            break;
        }

        case OP_SWAP: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t tmp = vm->stack[vm->sp - 1];
            vm->stack[vm->sp - 1] = vm->stack[vm->sp - 2];
            vm->stack[vm->sp - 2] = tmp;
            break;
        }

        case OP_ADD: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = a + b;
            break;
        }

        case OP_SUB: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = b - a;
            break;
        }

        case OP_MUL: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = a * b;
            break;
        }

        case OP_DIV: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            if (b == 0) { fprintf(stderr, "Division by zero\n"); return 1; }
            vm->stack[vm->sp++] = a / b;
            break;
        }

        case OP_MOD: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            if (b == 0) { fprintf(stderr, "Modulo by zero\n"); return 1; }
            vm->stack[vm->sp++] = a % b;
            break;
        }

        case OP_EQ: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (a == b) ? 1 : 0;
            break;
        }

        case OP_NE: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (a != b) ? 1 : 0;
            break;
        }

        case OP_LT: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (b < a) ? 1 : 0;
            break;
        }

        case OP_GT: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (b > a) ? 1 : 0;
            break;
        }

        case OP_LE: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (b <= a) ? 1 : 0;
            break;
        }

        case OP_GE: {
            if (vm->sp < 2) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t b = vm->stack[--vm->sp];
            int32_t a = vm->stack[--vm->sp];
            vm->stack[vm->sp++] = (b >= a) ? 1 : 0;
            break;
        }

        case OP_JMP: {
            if (vm->pc + 4 > vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            int32_t offset = read_i32(&vm->code[vm->pc]);
            vm->pc += 4;
            vm->pc = (uint32_t)((int32_t)vm->pc + offset);
            break;
        }

        case OP_JZ: {
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t val = vm->stack[--vm->sp];
            if (vm->pc + 4 > vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            int32_t offset = read_i32(&vm->code[vm->pc]);
            vm->pc += 4;
            if (val == 0) {
                vm->pc = (uint32_t)((int32_t)vm->pc + offset);
            }
            break;
        }

        case OP_JNZ: {
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t val = vm->stack[--vm->sp];
            if (vm->pc + 4 > vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            int32_t offset = read_i32(&vm->code[vm->pc]);
            vm->pc += 4;
            if (val != 0) {
                vm->pc = (uint32_t)((int32_t)vm->pc + offset);
            }
            break;
        }

        case OP_CALL: {
            if (vm->pc + 4 > vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            int32_t offset = read_i32(&vm->code[vm->pc]);
            vm->pc += 4;
            if (vm->cp >= MAX_CALLSTACK) { fprintf(stderr, "Call stack overflow\n"); return 1; }
            vm->call_stack[vm->cp++] = vm->pc - 5;
            vm->pc = (uint32_t)((int32_t)vm->pc + offset);
            break;
        }

        case OP_RET: {
            if (vm->cp <= 0) { fprintf(stderr, "Call stack underflow\n"); return 1; }
            vm->pc = vm->call_stack[--vm->cp];
            break;
        }

        case OP_LOAD: {
            if (vm->pc >= vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            uint8_t idx = vm->code[vm->pc++];
            if (idx >= MAX_LOCALS) { fprintf(stderr, "Local index out of range\n"); return 1; }
            if (vm->sp >= MAX_STACK) { fprintf(stderr, "Stack overflow\n"); return 1; }
            vm->stack[vm->sp++] = vm->locals[idx];
            break;
        }

        case OP_STORE: {
            if (vm->pc >= vm->code_len) { fprintf(stderr, "Unexpected EOF\n"); return 1; }
            uint8_t idx = vm->code[vm->pc++];
            if (idx >= MAX_LOCALS) { fprintf(stderr, "Local index out of range\n"); return 1; }
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            vm->locals[idx] = vm->stack[--vm->sp];
            break;
        }

        case OP_PRINT: {
            if (vm->sp <= 0) { fprintf(stderr, "Stack underflow\n"); return 1; }
            int32_t val = vm->stack[--vm->sp];
            printf("%d\n", val);
            break;
        }

        default:
            fprintf(stderr, "Unknown opcode: %u at pc=%u\n", op, vm->pc - 1);
            return 1;
        }
    }
    return 0;
}