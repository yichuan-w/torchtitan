#ifndef TYPES_H
#define TYPES_H

#include <stddef.h>

typedef enum {
    TYPE_NIL,
    TYPE_NUMBER,
    TYPE_SYMBOL,
    TYPE_CONS,
    TYPE_BUILTIN,
    TYPE_LAMBDA
} ValueType;

typedef struct Value Value;
typedef struct Env Env;

typedef Value* (*BuiltinFunc)(Value* args, Env* env);

struct Value {
    ValueType type;
    union {
        long number;
        char* symbol;
        struct { Value* car; Value* cdr; } cons;
        BuiltinFunc builtin;
        struct {
            Value* params;
            Value* body;
            Env* closure;
        } lambda;
    } data;
    Value* next;  /* for GC mark list */
    int marked;
};

/* Constructors */
Value* make_nil(void);
Value* make_number(long n);
Value* make_symbol(const char* name);
Value* make_cons(Value* car, Value* cdr);
Value* make_builtin(BuiltinFunc fn);
Value* make_lambda(Value* params, Value* body, Env* closure);

/* Type checks */
int is_nil(Value* v);
int is_number(Value* v);
int is_symbol(Value* v);
int is_cons(Value* v);
int is_list(Value* v);
int is_builtin(Value* v);
int is_lambda(Value* v);

/* Comparisons */
int values_equal(Value* a, Value* b);

/* List utilities */
Value* list_length(Value* list);
Value* list_nth(Value* list, long n);

/* Memory */
void* lisp_malloc(size_t size);
void lisp_free(void* ptr);
void gc_mark(Value* v);
void gc_sweep(void);

/* Globals */
extern Value* NIL;

#endif
