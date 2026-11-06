/*
 * CHIP-8 Interpreter
 *
 * Emulates the original CHIP-8 virtual machine as specified for
 * the COSMAC VIP microcomputer (RCA, 1977).
 *
 * Usage: ./chip8 <romfile>
 *
 * Output format (stdout):
 *   V0=XX V1=XX ... VF=XX I=XXXX PC=XXXX
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define MEM_SIZE        4096
#define STACK_SIZE      16
#define NUM_REGS        16
#define START_ADDR      0x200
#define FONTSET_START   0x050
#define MAX_CYCLES      50000

/* ── Font set (standard hex digits 0–F, 5 bytes each) ─────────────── */
static const uint8_t fontset[80] = {
    0xF0, 0x90, 0x90, 0x90, 0xF0, /* 0 */
    0x20, 0x60, 0x20, 0x20, 0x70, /* 1 */
    0xF0, 0x10, 0xF0, 0x80, 0xF0, /* 2 */
    0xF0, 0x10, 0xF0, 0x10, 0xF0, /* 3 */
    0x90, 0x90, 0xF0, 0x10, 0x10, /* 4 */
    0xF0, 0x80, 0xF0, 0x10, 0xF0, /* 5 */
    0xF0, 0x80, 0xF0, 0x90, 0xF0, /* 6 */
    0xF0, 0x10, 0x20, 0x40, 0x40, /* 7 */
    0xF0, 0x90, 0xF0, 0x90, 0xF0, /* 8 */
    0xF0, 0x90, 0xF0, 0x10, 0xF0, /* 9 */
    0xF0, 0x90, 0xF0, 0x90, 0x90, /* A */
    0xE0, 0x90, 0xE0, 0x90, 0xE0, /* B */
    0xF0, 0x80, 0x80, 0x80, 0xF0, /* C */
    0xE0, 0x90, 0x90, 0x90, 0xE0, /* D */
    0xF0, 0x80, 0xF0, 0x80, 0xF0, /* E */
    0xF0, 0x80, 0xF0, 0x80, 0x80  /* F */
};

/* ── VM State ─────────────────────────────────────────────────────── */
typedef struct {
    uint8_t  memory[MEM_SIZE];
    uint8_t  V[NUM_REGS];        /* V0–VF general-purpose registers   */
    uint16_t I;                  /* address index register             */
    uint16_t PC;                 /* program counter                    */
    uint8_t  SP;                 /* stack pointer                      */
    uint16_t stack[STACK_SIZE];
    uint8_t  delay_timer;
    uint8_t  sound_timer;
    int      halted;
} Chip8;

/* ── Disassembler (debug utility, not used during execution) ──────── */
static const char *disasm_op(uint16_t op) {
    static char buf[64];
    switch (op & 0xF000) {
    case 0x0000:
        if (op == 0x00E0) snprintf(buf, sizeof(buf), "CLS");
        else if (op == 0x00EE) snprintf(buf, sizeof(buf), "RET");
        else snprintf(buf, sizeof(buf), "SYS  %03X", op & 0x0FFF);
        break;
    case 0x1000: snprintf(buf, sizeof(buf), "JP   %03X", op & 0x0FFF); break;
    case 0x2000: snprintf(buf, sizeof(buf), "CALL %03X", op & 0x0FFF); break;
    case 0x3000: snprintf(buf, sizeof(buf), "SE   V%X, %02X", (op>>8)&0xF, op&0xFF); break;
    case 0x4000: snprintf(buf, sizeof(buf), "SNE  V%X, %02X", (op>>8)&0xF, op&0xFF); break;
    case 0x5000: snprintf(buf, sizeof(buf), "SE   V%X, V%X", (op>>8)&0xF, (op>>4)&0xF); break;
    case 0x6000: snprintf(buf, sizeof(buf), "LD   V%X, %02X", (op>>8)&0xF, op&0xFF); break;
    case 0x7000: snprintf(buf, sizeof(buf), "ADD  V%X, %02X", (op>>8)&0xF, op&0xFF); break;
    case 0x8000: {
        uint8_t x = (op>>8)&0xF, y = (op>>4)&0xF;
        switch (op & 0xF) {
        case 0x0: snprintf(buf, sizeof(buf), "LD   V%X, V%X", x, y); break;
        case 0x1: snprintf(buf, sizeof(buf), "OR   V%X, V%X", x, y); break;
        case 0x2: snprintf(buf, sizeof(buf), "AND  V%X, V%X", x, y); break;
        case 0x3: snprintf(buf, sizeof(buf), "XOR  V%X, V%X", x, y); break;
        case 0x4: snprintf(buf, sizeof(buf), "ADD  V%X, V%X", x, y); break;
        case 0x5: snprintf(buf, sizeof(buf), "SUB  V%X, V%X", x, y); break;
        case 0x6: snprintf(buf, sizeof(buf), "SHR  V%X {, V%X}", x, y); break;
        case 0x7: snprintf(buf, sizeof(buf), "SUBN V%X, V%X", x, y); break;
        case 0xE: snprintf(buf, sizeof(buf), "SHL  V%X {, V%X}", x, y); break;
        default:  snprintf(buf, sizeof(buf), "???            "); break;
        }
        break;
    }
    case 0x9000: snprintf(buf, sizeof(buf), "SNE  V%X, V%X", (op>>8)&0xF, (op>>4)&0xF); break;
    case 0xA000: snprintf(buf, sizeof(buf), "LD   I, %03X", op & 0x0FFF); break;
    case 0xB000: snprintf(buf, sizeof(buf), "JP   V0, %03X", op & 0x0FFF); break;
    case 0xC000: snprintf(buf, sizeof(buf), "RND  V%X, %02X", (op>>8)&0xF, op&0xFF); break;
    case 0xD000: snprintf(buf, sizeof(buf), "DRW  V%X, V%X, %X", (op>>8)&0xF, (op>>4)&0xF, op&0xF); break;
    case 0xE000:
        if ((op&0xFF) == 0x9E) snprintf(buf, sizeof(buf), "SKP  V%X", (op>>8)&0xF);
        else snprintf(buf, sizeof(buf), "SKNP V%X", (op>>8)&0xF);
        break;
    case 0xF000: {
        uint8_t x = (op>>8)&0xF;
        switch (op & 0xFF) {
        case 0x07: snprintf(buf, sizeof(buf), "LD   V%X, DT", x); break;
        case 0x0A: snprintf(buf, sizeof(buf), "LD   V%X, K", x); break;
        case 0x15: snprintf(buf, sizeof(buf), "LD   DT, V%X", x); break;
        case 0x18: snprintf(buf, sizeof(buf), "LD   ST, V%X", x); break;
        case 0x1E: snprintf(buf, sizeof(buf), "ADD  I, V%X", x); break;
        case 0x29: snprintf(buf, sizeof(buf), "LD   F, V%X", x); break;
        case 0x33: snprintf(buf, sizeof(buf), "LD   B, V%X", x); break;
        case 0x55: snprintf(buf, sizeof(buf), "LD   [I], V%X", x); break;
        case 0x65: snprintf(buf, sizeof(buf), "LD   V%X, [I]", x); break;
        default:   snprintf(buf, sizeof(buf), "???            "); break;
        }
        break;
    }
    default: snprintf(buf, sizeof(buf), "???            "); break;
    }
    return buf;
}

/* ── State dump utility ───────────────────────────────────────────── */
static void dump_state(const Chip8 *c) {
    printf("V0=%02X V1=%02X V2=%02X V3=%02X V4=%02X V5=%02X V6=%02X V7=%02X "
           "V8=%02X V9=%02X VA=%02X VB=%02X VC=%02X VD=%02X VE=%02X VF=%02X "
           "I=%04X PC=%04X\n",
           c->V[0], c->V[1], c->V[2], c->V[3],
           c->V[4], c->V[5], c->V[6], c->V[7],
           c->V[8], c->V[9], c->V[10], c->V[11],
           c->V[12], c->V[13], c->V[14], c->V[15],
           c->I, c->PC);
}

/* ── Hex dump helper (utility, not used during normal execution) ──── */
static void hexdump(const uint8_t *data, size_t len, uint16_t base_addr) {
    for (size_t i = 0; i < len; i += 16) {
        printf("%04X: ", (unsigned)(base_addr + i));
        for (size_t j = 0; j < 16 && (i + j) < len; j++)
            printf("%02X ", data[i + j]);
        printf("\n");
    }
}

/* ── ROM checksum (utility for debugging) ─────────────────────────── */
static uint16_t rom_checksum(const uint8_t *rom, size_t len) {
    uint16_t sum = 0;
    for (size_t i = 0; i < len; i++) sum += rom[i];
    return sum;
}

/* ── Initialisation ────────────────────────────────────────────────── */
static void init_chip8(Chip8 *c) {
    memset(c, 0, sizeof(*c));
    c->PC = START_ADDR;
    memcpy(&c->memory[FONTSET_START], fontset, sizeof(fontset));
}

/* ── ROM loader ───────────────────────────────────────────────────── */
static int load_rom(Chip8 *c, const char *filename) {
    FILE *f = fopen(filename, "rb");
    if (!f) {
        fprintf(stderr, "Error: cannot open ROM '%s'\n", filename);
        return 0;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 0 || (size_t)size > MEM_SIZE - START_ADDR) {
        fprintf(stderr, "Error: ROM too large (%ld bytes)\n", size);
        fclose(f);
        return 0;
    }
    size_t n = fread(&c->memory[START_ADDR], 1, (size_t)size, f);
    fclose(f);
    if (n != (size_t)size) {
        fprintf(stderr, "Error: failed to read ROM\n");
        return 0;
    }
    return 1;
}

/* ── Execute one instruction ───────────────────────────────────────── */
static void emulate_cycle(Chip8 *c) {
    if (c->halted) return;

    uint16_t op = ((uint16_t)c->memory[c->PC] << 8) | c->memory[c->PC + 1];
    c->PC += 2;

    uint8_t  x   = (op >> 8) & 0x0F;
    uint8_t  y   = (op >> 4) & 0x0F;
    uint8_t  n   = op & 0x0F;
    uint8_t  nn  = op & 0xFF;
    uint16_t nnn = op & 0x0FFF;

    switch (op & 0xF000) {

    case 0x0000:
        if (op == 0x00E0) {
            /* Clear display — no-op in headless mode */
        } else if (op == 0x00EE) {
            /* Return */
            if (c->SP == 0) { c->halted = 1; return; }
            c->SP--;
            c->PC = c->stack[c->SP];
        }
        /* 0NNN: RCA 1802 machine-language routine call — ignored */
        break;

    case 0x1000: /* 1NNN — Jump */
        c->PC = nnn;
        break;

    case 0x2000: /* 2NNN — Call */
        if (c->SP >= STACK_SIZE) { c->halted = 1; return; }
        c->stack[c->SP] = c->PC;
        c->SP++;
        c->PC = nnn;
        break;

    case 0x3000: /* 3XNN — Skip if VX == NN */
        if (c->V[x] == nn) c->PC += 2;
        break;

    case 0x4000: /* 4XNN — Skip if VX != NN */
        if (c->V[x] != nn) c->PC += 2;
        break;

    case 0x5000: /* 5XY0 — Skip if VX == VY */
        if (c->V[x] == c->V[y]) c->PC += 2;
        break;

    case 0x6000: /* 6XNN — VX = NN */
        c->V[x] = nn;
        break;

    case 0x7000: /* 7XNN — VX += NN */
        c->V[x] += nn;
        break;

    case 0x8000:
        switch (n) {
        case 0x0: /* LD VX, VY */
            c->V[x] = c->V[y];
            break;
        case 0x1: /* OR VX, VY */
            c->V[x] |= c->V[y];
            break;
        case 0x2: /* AND VX, VY */
            c->V[x] &= c->V[y];
            break;
        case 0x3: /* XOR VX, VY */
            c->V[x] ^= c->V[y];
            break;
        case 0x4: /* ADD VX, VY */
            {
                uint16_t sum = (uint16_t)c->V[x] + (uint16_t)c->V[y];
                c->V[0xF] = (sum > 0xFF) ? 1 : 0;
                c->V[x] = (uint8_t)(sum & 0xFF);
            }
            break;
        case 0x5: /* SUB VX, VY */
            c->V[0xF] = (c->V[x] >= c->V[y]) ? 1 : 0;
            c->V[x] -= c->V[y];
            break;
        case 0x6: /* SHR VX — shift VX right, store LSB in VF */
            c->V[0xF] = c->V[x] & 0x01;
            c->V[x] >>= 1;
            break;
        case 0x7: /* SUBN VX, VY — VX = VY - VX */
            c->V[0xF] = (c->V[y] >= c->V[x]) ? 1 : 0;
            c->V[x] = c->V[y] - c->V[x];
            break;
        case 0xE: /* SHL VX — shift VX left, store MSB in VF */
            c->V[0xF] = (c->V[x] & 0x80) >> 7;
            c->V[x] <<= 1;
            break;
        default:
            break;
        }
        break;

    case 0x9000: /* 9XY0 — Skip if VX != VY */
        if (c->V[x] != c->V[y]) c->PC += 2;
        break;

    case 0xA000: /* ANNN — I = NNN */
        c->I = nnn;
        break;

    case 0xB000: /* BNNN — Jump to V0 + NNN */
        c->PC = (uint16_t)c->V[0] + nnn;
        break;

    case 0xC000: /* CXNN — VX = random byte & NN */
        c->V[x] = (uint8_t)(rand() & nn);
        break;

    case 0xD000: /* DXYN — Draw sprite (headless: no-op) */
        break;

    case 0xE000:
        if (nn == 0x9E) {
            /* EX9E: Skip if key pressed — no keyboard, never skip */
        } else if (nn == 0xA1) {
            /* EXA1: Skip if key not pressed — always skip */
            c->PC += 2;
        }
        break;

    case 0xF000:
        switch (nn) {
        case 0x07: /* LD VX, DT */
            c->V[x] = c->delay_timer;
            break;
        case 0x0A: /* LD VX, K — wait for key (headless: return 0xFF) */
            c->V[x] = 0xFF;
            break;
        case 0x15: /* LD DT, VX */
            c->delay_timer = c->V[x];
            break;
        case 0x18: /* LD ST, VX */
            c->sound_timer = c->V[x];
            break;
        case 0x1E: /* ADD I, VX */
            c->I += c->V[x];
            break;
        case 0x29: /* LD F, VX — font character address */
            c->I = FONTSET_START + (c->V[x] & 0x0F) * 5;
            break;
        case 0x33: /* LD B, VX — BCD conversion */
            {
                uint8_t val = c->V[x];
                c->memory[c->I]     = val % 10;
                c->memory[c->I + 1] = (val / 10) % 10;
                c->memory[c->I + 2] = val / 100;
            }
            break;
        case 0x55: /* LD [I], VX — store registers to memory */
            for (uint8_t i = 0; i <= x; i++) {
                c->memory[c->I + i] = c->V[i];
            }
            break;
        case 0x65: /* LD VX, [I] — load registers from memory */
            for (uint8_t i = 0; i <= x; i++) {
                c->V[i] = c->memory[c->I + i];
            }
            break;
        default:
            break;
        }
        break;

    default:
        break;
    }

    /* Guard: halt if PC goes past addressable memory */
    if (c->PC >= MEM_SIZE - 2) c->halted = 1;
}

/* ── Main execution loop ───────────────────────────────────────────── */
static void run(Chip8 *c) {
    int cycles = 0;
    uint16_t last_pc = c->PC;
    int same_pc_count = 0;

    while (!c->halted && cycles < MAX_CYCLES) {
        emulate_cycle(c);
        cycles++;

        /* Timers decrement at ~60 Hz */
        if (cycles % 9 == 0) {
            if (c->delay_timer > 0) c->delay_timer--;
            if (c->sound_timer > 0) c->sound_timer--;
        }

        /* Halt detection: repeated jumps to same address */
        if (c->PC == last_pc) {
            same_pc_count++;
            if (same_pc_count > 5) break;
        } else {
            same_pc_count = 0;
            last_pc = c->PC;
        }
    }
}

/* ── Entry point ───────────────────────────────────────────────────── */
int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <romfile>\n", argv[0]);
        return 1;
    }

    srand((unsigned)time(NULL));

    Chip8 chip8;
    init_chip8(&chip8);

    if (!load_rom(&chip8, argv[1])) {
        return 1;
    }

    run(&chip8);
    dump_state(&chip8);

    return 0;
}
