#include "printer.h"
#include <stdio.h>
#include <string.h>

static void print_list(Value* list);
static void print_value_internal(Value* v);

static void print_value_internal(Value* v) {
    if (!v) {
        printf("nil");
        return;
    }
    
    switch (v->type) {
    case TYPE_NIL:
        printf("nil");
        break;
    case TYPE_NUMBER:
        printf("%lu", (unsigned long)v->data.number);
        break;
    case TYPE_SYMBOL:
        printf("%s", v->data.symbol);
        break;
    case TYPE_CONS:
        printf("(");
        print_list(v);
        printf(")");
        break;
    case TYPE_BUILTIN:
        printf("#<builtin>");
        break;
    case TYPE_LAMBDA:
        printf("#<lambda>");
        break;
    }
}

static void print_list(Value* list) {
    if (is_nil(list)) {
        return;
    }
    
    print_value_internal(list->data.cons.car);
    
    Value* rest = list->data.cons.cdr;
    if (is_cons(rest)) {
        printf(" ");
        print_list(rest);
    } else if (!is_nil(rest)) {
        printf(" . ");
        print_value_internal(rest);
    }
}

void print_value(Value* v) {
    print_value_internal(v);
    printf("\n");
}

void print_value_to_buf(Value* v, char* buf, int* pos) {
    /* Simplified: just use a temp file */
    /* Not implemented for simplicity */
}
