FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# No Lua runtime installed — the solver must implement the interpreter
COPY files/program.luac /app/program.luac
COPY files/prog2.luac /app/prog2.luac
COPY files/luavm.py /app/luavm.py
