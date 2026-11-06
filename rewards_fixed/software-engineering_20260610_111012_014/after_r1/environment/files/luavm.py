#!/usr/bin/env python3
"""A small Lua 5.1 bytecode interpreter sufficient for the supplied program."""
import math
import struct
import sys

# Lua 5.1 opcodes
OP_MOVE, OP_LOADK, OP_LOADBOOL, OP_LOADNIL, OP_GETUPVAL, OP_GETGLOBAL, OP_GETTABLE, OP_SETGLOBAL, OP_SETUPVAL, OP_SETTABLE, OP_NEWTABLE, OP_SELF, OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_POW, OP_UNM, OP_NOT, OP_LEN, OP_CONCAT, OP_JMP, OP_EQ, OP_LT, OP_LE, OP_TEST, OP_TESTSET, OP_CALL, OP_TAILCALL, OP_RETURN, OP_FORLOOP, OP_FORPREP, OP_TFORLOOP, OP_SETLIST, OP_CLOSE, OP_CLOSURE, OP_VARARG = range(38)


class LuaError(Exception):
    pass


class Reader:
    def __init__(self, data, little=True):
        self.data = data
        self.pos = 0
        self.little = little
        self.u16fmt = "<H" if little else ">H"
        self.u32fmt = "<I" if little else ">I"
        self.i32fmt = "<i" if little else ">i"
        self.u64fmt = "<Q" if little else ">Q"
        self.dfmt = "<d" if little else ">d"

    def take(self, n):
        b = self.data[self.pos:self.pos + n]
        if len(b) != n:
            raise LuaError("unexpected end of bytecode")
        self.pos += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack(self.u32fmt, self.take(4))[0]

    def i32(self):
        return struct.unpack(self.i32fmt, self.take(4))[0]

    def size_t(self):
        return struct.unpack(self.u64fmt, self.take(8))[0]

    def number(self):
        return struct.unpack(self.dfmt, self.take(8))[0]

    def string(self):
        n = self.size_t()
        if n == 0:
            return ""
        raw = self.take(n - 1)
        self.u8()  # trailing NUL
        return raw.decode("latin1")


class Proto:
    pass


def read_proto(r):
    p = Proto()
    p.source = r.string()
    p.line_defined = r.i32()
    p.last_line_defined = r.i32()
    p.nups = r.u8()
    p.numparams = r.u8()
    p.is_vararg = r.u8()
    p.maxstack = r.u8()

    n = r.i32()
    p.code = [r.u32() for _ in range(n)]

    n = r.i32()
    p.constants = []
    for _ in range(n):
        t = r.u8()
        if t == 0:
            p.constants.append(None)
        elif t == 1:
            p.constants.append(bool(r.u8()))
        elif t == 3:
            p.constants.append(r.number())
        elif t == 4:
            p.constants.append(r.string())
        else:
            raise LuaError("unsupported constant type %d" % t)

    n = r.i32()
    p.protos = [read_proto(r) for _ in range(n)]

    n = r.i32()
    p.lineinfo = [r.i32() for _ in range(n)]

    n = r.i32()
    p.locvars = []
    for _ in range(n):
        p.locvars.append((r.string(), r.i32(), r.i32()))

    n = r.i32()
    p.upvalue_names = [r.string() for _ in range(n)]
    return p


def parse_chunk(path):
    with open(path, "rb") as f:
        data = f.read()
    r = Reader(data)
    if r.take(4) != b"\x1bLua":
        raise LuaError("not a Lua bytecode file")
    version = r.u8()
    if version != 0x51:
        raise LuaError("only Lua 5.1 bytecode is supported")
    r.u8()  # format
    endian = r.u8()
    int_size = r.u8()
    size_t_size = r.u8()
    instruction_size = r.u8()
    number_size = r.u8()
    r.u8()  # integral flag
    if endian != 1:
        raise LuaError("only little-endian Lua 5.1 bytecode is supported")
    if int_size != 4 or size_t_size != 8 or instruction_size != 4 or number_size != 8:
        raise LuaError("unsupported Lua 5.1 binary layout")
    return read_proto(r)


# ---------------------------------------------------------------------------
# Runtime values


def execute(proto):
    """Execute a parsed prototype. Implement the Lua 5.1 VM here."""
    raise NotImplementedError("Lua 5.1 execution is not implemented")


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python3 luavm.py <bytecode.luac>\n")
        return 2
    try:
        execute(parse_chunk(sys.argv[1]))
    except (LuaError, NotImplementedError) as error:
        sys.stderr.write("lua error: %s\n" % error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
