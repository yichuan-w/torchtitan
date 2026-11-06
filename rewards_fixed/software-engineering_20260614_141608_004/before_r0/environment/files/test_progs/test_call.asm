; Test function call

JMP main
square:
LOAD 0
DUP
MUL
STORE 0
RET
main:
PUSH 7
STORE 0
CALL square
LOAD 0
PRINT
HALT