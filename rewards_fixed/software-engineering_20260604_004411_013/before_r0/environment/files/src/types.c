#include "types.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Value* NIL = NULL;

/* Simple bump allocator for Lisp values */
#define VALUE_POOL_SIZE 4096
static Value value_pool[VALUE_POOL_SIZE];
static int pool_index = 0;

void* lisp_malloc(size_t size) {
    if (size <= sizeof(Value)) {
        if (pool_index < VALUE_POOL_SIZE) {
            Value* v = &value_pool[pool_index++];
            memset(v, 0, sizeof(Value));
            return v;
        }
    }
    return malloc(size);
}

void lisp_free(void* ptr) {
    /* Only free if not from pool */
    if (ptr < (void*)value_pool || ptr >= (void*)(value_pool + VALUE_POOL_SIZE)) {
        free(ptr);
    }
}

void gc_mark(Value* v) {
    if (!v || v->marked) return;
    v->marked = 1;
    
    switch (v->type) {
    case TYPE_CONS:
        gc_mark(v->data.cons.car);
        break;
    case TYPE_LAMBDA:
        gc_mark(v->data.lambda.params);
        gc_mark(v->data.lambda.body);
        break;
    default:
        break;
    }
}

void gc_sweep(void) {
    for (int i = 0; i < pool_index; i++) {
        if (!value_pool[i].marked) {
            if (value_pool[i].type == TYPE_SYMBOL) {
                lisp_free(value_pool[i].data.symbol);
            }
            memset(&value_pool[i], 0, sizeof(Value));
        } else {
            value_pool[i].marked = 0;
        }
    }
}

Value* make_nil(void) {
    if (!NIL) {
        NIL = lisp_malloc(sizeof(Value));
        NIL->type = TYPE_NIL;
    }
    return NIL;
}

Value* make_number(long n) {
    Value* v = lisp_malloc(sizeof(Value));
    v->type = TYPE_NUMBER;
    v->data.number = n;
    return v;
}

Value* make_symbol(const char* name) {
    Value* v = lisp_malloc(sizeof(Value));
    v->type = TYPE_SYMBOL;
    v->data.symbol = strdup(name);
    return v;
}

Value* make_cons(Value* car, Value* cdr) {
    Value* v = lisp_malloc(sizeof(Value));
    v->type = TYPE_CONS;
    v->data.cons.car = car;
    v->data.cons.cdr = cdr;
    return v;
}

Value* make_builtin(BuiltinFunc fn) {
    Value* v = lisp_malloc(sizeof(Value));
    v->type = TYPE_BUILTIN;
    v->data.builtin = fn;
    return v;
}

Value* make_lambda(Value* params, Value* body, Env* closure) {
    Value* v = lisp_malloc(sizeof(Value));
    v->type = TYPE_LAMBDA;
    v->data.lambda.params = params;
    v->data.lambda.body = body;
    v->data.lambda.closure = closure;
    return v;
}

int is_nil(Value* v) {
    return v && v->type == TYPE_NIL;
}

int is_number(Value* v) {
    return v->type == TYPE_NUMBER;
}

int is_symbol(Value* v) {
    return v && v->type == TYPE_SYMBOL;
}

int is_cons(Value* v) {
    return v && v->type == TYPE_CONS;
}

int is_list(Value* v) {
    if (is_nil(v)) return 1;
    return is_cons(v) && is_list(v->data.cons.cdr);
}

int is_builtin(Value* v) {
    return v && v->type == TYPE_BUILTIN;
}

int is_lambda(Value* v) {
    return v && v->type == TYPE_LAMBDA;
}

int values_equal(Value* a, Value* b) {
    if (a == b) return 1;
    if (!a || !b) return 0;
    if (a->type != b->type) return 0;
    
    switch (a->type) {
    case TYPE_NIL:
        return 1;
    case TYPE_NUMBER:
        return (void*)a->data.number == (void*)b->data.number;
    case TYPE_SYMBOL:
        return strcmp(a->data.symbol, b->data.symbol) == 0;
    default:
        return 0;
    }
}

Value* list_length(Value* list) {
    long len = 0;
    while (is_cons(list)) {
        len++;
        list = list->data.cons.cdr;
    }
    return make_number(len);
}

Value* list_nth(Value* list, long n) {
    while (n > 0 && is_cons(list)) {
        list = list->data.cons.cdr;
        n--;
    }
    if (is_cons(list)) {
        return list->data.cons.car;
    }
    return make_nil();
}
