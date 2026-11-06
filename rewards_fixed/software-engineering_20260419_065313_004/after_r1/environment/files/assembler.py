"""Small assembler for the bytecode format implemented by vm.c."""


class Assembler:
    HALT = 0x00
    PUSH = 0x01
    LOAD = 0x02
    STORE = 0x03
    DUP = 0x04
    SWAP = 0x05
    POP = 0x06
    ADD = 0x10
    SUB = 0x11
    MUL = 0x12
    DIV = 0x13
    MOD = 0x14
    NEG = 0x15
    AND = 0x16
    OR = 0x17
    XOR = 0x18
    NOT = 0x19
    SHL = 0x1A
    SHR = 0x1B
    EQ = 0x20
    NE = 0x21
    LT = 0x22
    LE = 0x23
    GT = 0x24
    GE = 0x25
    JMP = 0x30
    JZ = 0x31
    JNZ = 0x32
    CALL = 0x40
    RET = 0x41
    PRINT = 0xF0

    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, opcode):
        self.code.append(opcode)

    def push(self, value):
        value &= 0xFFFF
        self.code.extend((self.PUSH, value & 0xFF, value >> 8))

    def load(self, index):
        self.code.extend((self.LOAD, index))

    def store(self, index):
        self.code.extend((self.STORE, index))

    def label(self, name):
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = len(self.code)

    def jump(self, opcode, label):
        if opcode not in (self.JMP, self.JZ, self.JNZ, self.CALL):
            raise ValueError(f"opcode 0x{opcode:02x} does not take an address")
        self.code.extend((opcode, 0, 0))
        self.fixups.append((len(self.code) - 2, label))

    def assemble(self):
        result = bytearray(self.code)
        for position, name in self.fixups:
            if name not in self.labels:
                raise ValueError(f"undefined label: {name}")
            address = self.labels[name]
            result[position] = address & 0xFF
            result[position + 1] = address >> 8
        return bytes(result)

    def write(self, path="/app/prog.bin"):
        data = self.assemble()
        with open(path, "wb") as output:
            output.write(data)
        return len(data)
