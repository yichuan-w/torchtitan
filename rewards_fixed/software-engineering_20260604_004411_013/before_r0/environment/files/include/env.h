#ifndef ENV_H
#define ENV_H

#include "types.h"

struct Env {
    Value* symbol;
    Value* value;
    Env* next;   /* next binding in this frame */
    Env* parent; /* parent environment */
};

Env* env_create(Env* parent);
void env_set(Env* env, const char* symbol, Value* value);
Value* env_get(Env* env, const char* symbol);
Env* env_find(Env* env, const char* symbol);

#endif
