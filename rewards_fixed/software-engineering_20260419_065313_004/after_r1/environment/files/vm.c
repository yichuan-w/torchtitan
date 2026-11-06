/*
 * Stack-based bytecode virtual machine interpreter.
 * 
 * The VM executes bytecode programs from files. Each instruction is encoded
 * as a single byte opcode followed by optional operands.
 *
 * Memory layout:
 * - 256 local variables (indexed 0-255)
 * - Operand stack (max 256 entries)
 * - Input value is pre-loaded in variable 0
 * - Output value should be stored in variable 1 before HALT
 *
 * Instruction encoding:
 * - Opcode byte followed by 0-2 operand bytes depending on the instruction
 * - See opcode definitions below
 *
 * Execution starts at the first byte and continues until HALT or end of program.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#define STACK_SIZE 256
#define NUM_VARS 256
#define MAX_PROGRAM 65536

/* Opcodes */
#define OP_HALT    0x00  /* Halt execution */
#define OP_PUSH    0x01  /* Push immediate 16-bit value: PUSH lo hi */
#define OP_LOAD    0x02  /* Load variable: LOAD idx */
#define OP_STORE   0x03  /* Store variable: STORE idx */
#define OP_DUP     0x04  /* Duplicate top of stack */
#define OP_SWAP    0x05  /* Swap top two stack elements */
#define OP_POP     0x06  /* Pop and discard top of stack */
#define OP_ADD     0x10  /* Pop b, pop a, push a+b */
#define OP_SUB     0x11  /* Pop b, pop a, push a-b */
#define OP_MUL     0x12  /* Pop b, pop a, push a*b */
#define OP_DIV     0x13  /* Pop b, pop a, push a/b (signed, truncates toward zero) */
#define OP_MOD     0x14  /* Pop b, pop a, push a%b (signed, follows C99 semantics) */
#define OP_NEG     0x15  /* Negate top of stack */
#define OP_AND     0x16  /* Bitwise AND */
#define OP_OR      0x17  /* Bitwise OR */
#define OP_XOR     0x18  /* Bitwise XOR */
#define OP_NOT     0x19  /* Bitwise NOT */
#define OP_SHL     0x1A  /* Left shift */
#define OP_SHR     0x1B  /* Arithmetic right shift */
#define OP_EQ      0x20  /* Pop b, pop a, push 1 if a==b else 0 */
#define OP_NE      0x21  /* Pop b, pop a, push 1 if a!=b else 0 */
#define OP_LT      0x22  /* Pop b, pop a, push 1 if a<b else 0 (signed) */
#define OP_LE      0x23  /* Pop b, pop a, push 1 if a<=b else 0 */
#define OP_GT      0x24  /* Pop b, pop a, push 1 if a>b else 0 */
#define OP_GE      0x25  /* Pop b, pop a, push 1 if a>=b else 0 */
#define OP_JMP     0x30  /* Unconditional jump: JMP addr_lo addr_hi */
#define OP_JZ      0x31  /* Jump if zero: JZ addr_lo addr_hi */
#define OP_JNZ     0x32  /* Jump if not zero: JNZ addr_lo addr_hi */
#define OP_CALL    0x40  /* Call subroutine: CALL addr_lo addr_hi */
#define OP_RET     0x41  /* Return from subroutine */
#define OP_PRINT   0xF0  /* Debug: print top of stack (for debugging only) */

typedef struct {
    int32_t stack[STACK_SIZE];
    int sp;  /* Stack pointer (points to next free slot) */
    
    int32_t vars[NUM_VARS];
    
    uint8_t program[MAX_PROGRAM];
    int program_size;
    int pc;  /* Program counter */
    
    /* Call stack for subroutines */
    int call_stack[64];
    int call_sp;
    
    int instruction_count;
    int max_instructions;
} VM;

static int vm_halt = 0;

void vm_init(VM *vm) {
    memset(vm, 0, sizeof(VM));
    vm->sp = 0;
    vm->pc = 0;
    vm->call_sp = 0;
    vm->instruction_count = 0;
    vm->max_instructions = 10000000;  /* 10 million instructions max */
}

int vm_load(VM *vm, const char *filename) {
    FILE *f = fopen(filename, "rb");
    if (!f) {
        fprintf(stderr, "Error: Cannot open file '%s'\n", filename);
        return -1;
    }
    
    vm->program_size = fread(vm->program, 1, MAX_PROGRAM, f);
    fclose(f);
    
    if (vm->program_size == 0) {
        fprintf(stderr, "Error: Empty program\n");
        return -1;
    }
    
    return 0;
}

void vm_push(VM *vm, int32_t value) {
    if (vm->sp >= STACK_SIZE) {
        fprintf(stderr, "Stack overflow at pc=%d\n", vm->pc);
        vm_halt = 1;
        return;
    }
    vm->stack[vm->sp++] = value;
}

int32_t vm_pop(VM *vm) {
    if (vm->sp <= 0) {
        fprintf(stderr, "Stack underflow at pc=%d\n", vm->pc);
        vm_halt = 1;
        return 0;
    }
    return vm->stack[--vm->sp];
}

int32_t vm_peek(VM *vm) {
    if (vm->sp <= 0) {
        fprintf(stderr, "Stack underflow (peek) at pc=%d\n", vm->pc);
        vm_halt = 1;
        return 0;
    }
    return vm->stack[vm->sp - 1];
}

uint16_t vm_read_addr(VM *vm) {
    uint8_t lo = vm->program[vm->pc++];
    uint8_t hi = vm->program[vm->pc++];
    return (hi << 8) | lo;
}

int16_t vm_read_imm16(VM *vm) {
    uint8_t lo = vm->program[vm->pc++];
    uint8_t hi = vm->program[vm->pc++];
    return (int16_t)((hi << 8) | lo);
}

int vm_step(VM *vm) {
    if (vm->pc >= vm->program_size) {
        return 0;  /* End of program */
    }
    
    if (vm->instruction_count >= vm->max_instructions) {
        fprintf(stderr, "Instruction limit exceeded\n");
        return 0;
    }
    
    vm->instruction_count++;
    
    uint8_t op = vm->program[vm->pc++];
    
    int32_t a, b;
    uint16_t addr;
    
    switch (op) {
        case OP_HALT:
            return 0;
            
        case OP_PUSH:
            vm_push(vm, vm_read_imm16(vm));
            break;
            
        case OP_LOAD:
            vm_push(vm, vm->vars[vm->program[vm->pc++]]);
            break;
            
        case OP_STORE:
            vm->vars[vm->program[vm->pc++]] = vm_pop(vm);
            break;
            
        case OP_DUP:
            a = vm_peek(vm);
            vm_push(vm, a);
            break;
            
        case OP_SWAP:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, b);
            vm_push(vm, a);
            break;
            
        case OP_POP:
            vm_pop(vm);
            break;
            
        case OP_ADD:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a + b);
            break;
            
        case OP_SUB:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a - b);
            break;
            
        case OP_MUL:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a * b);
            break;
            
        case OP_DIV:
            b = vm_pop(vm);
            a = vm_pop(vm);
            if (b == 0) {
                fprintf(stderr, "Division by zero at pc=%d\n", vm->pc - 1);
                vm_halt = 1;
                vm_push(vm, 0);
            } else {
                /* C99 signed division truncates toward zero */
                vm_push(vm, a / b);
            }
            break;
            
        case OP_MOD:
            b = vm_pop(vm);
            a = vm_pop(vm);
            if (b == 0) {
                fprintf(stderr, "Modulo by zero at pc=%d\n", vm->pc - 1);
                vm_halt = 1;
                vm_push(vm, 0);
            } else {
                /* C99 modulo: result has same sign as dividend */
                vm_push(vm, a % b);
            }
            break;
            
        case OP_NEG:
            a = vm_pop(vm);
            vm_push(vm, -a);
            break;
            
        case OP_AND:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a & b);
            break;
            
        case OP_OR:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a | b);
            break;
            
        case OP_XOR:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a ^ b);
            break;
            
        case OP_NOT:
            a = vm_pop(vm);
            vm_push(vm, ~a);
            break;
            
        case OP_SHL:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a << (b & 31));
            break;
            
        case OP_SHR:
            b = vm_pop(vm);
            a = vm_pop(vm);
            /* Arithmetic right shift for signed values */
            vm_push(vm, a >> (b & 31));
            break;
            
        case OP_EQ:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a == b ? 1 : 0);
            break;
            
        case OP_NE:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a != b ? 1 : 0);
            break;
            
        case OP_LT:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a < b ? 1 : 0);
            break;
            
        case OP_LE:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a <= b ? 1 : 0);
            break;
            
        case OP_GT:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a > b ? 1 : 0);
            break;
            
        case OP_GE:
            b = vm_pop(vm);
            a = vm_pop(vm);
            vm_push(vm, a >= b ? 1 : 0);
            break;
            
        case OP_JMP:
            addr = vm_read_addr(vm);
            vm->pc = addr;
            break;
            
        case OP_JZ:
            addr = vm_read_addr(vm);
            a = vm_pop(vm);
            if (a == 0) {
                vm->pc = addr;
            }
            break;
            
        case OP_JNZ:
            addr = vm_read_addr(vm);
            a = vm_pop(vm);
            if (a != 0) {
                vm->pc = addr;
            }
            break;
            
        case OP_CALL:
            addr = vm_read_addr(vm);
            if (vm->call_sp >= 64) {
                fprintf(stderr, "Call stack overflow at pc=%d\n", vm->pc);
                vm_halt = 1;
            } else {
                vm->call_stack[vm->call_sp++] = vm->pc;
                vm->pc = addr;
            }
            break;
            
        case OP_RET:
            if (vm->call_sp <= 0) {
                fprintf(stderr, "Call stack underflow at pc=%d\n", vm->pc);
                vm_halt = 1;
            } else {
                vm->pc = vm->call_stack[--vm->call_sp];
            }
            break;
            
        case OP_PRINT:
            a = vm_peek(vm);
            fprintf(stderr, "[DEBUG] %d\n", a);
            break;
            
        default:
            fprintf(stderr, "Unknown opcode 0x%02X at pc=%d\n", op, vm->pc - 1);
            vm_halt = 1;
            break;
    }
    
    return 1;
}

int vm_run(VM *vm) {
    vm_halt = 0;
    while (!vm_halt && vm_step(vm)) {
        /* Continue execution */
    }
    return vm->vars[1];  /* Return value is in variable 1 */
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <program.bin> <input>\n", argv[0]);
        return 1;
    }
    
    VM vm;
    vm_init(&vm);
    
    if (vm_load(&vm, argv[1]) < 0) {
        return 1;
    }
    
    /* Input value goes in variable 0 */
    vm.vars[0] = atoi(argv[2]);
    
    int result = vm_run(&vm);
    printf("%d\n", result);
    
    return 0;
}
