#include "env.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

Env* env_create(Env* parent) {
    Env* e = malloc(sizeof(Env));
    e->symbol = NULL;
    e->value = NULL;
    e->next = NULL;
    e->parent = parent;
    return e;
}

void env_set(Env* env, const char* symbol, Value* value) {
    Env* entry = malloc(sizeof(Env));
    entry->symbol = make_symbol(symbol);
    entry->value = value;
    entry->next = env->next;
    env->next = entry;
    entry->parent = NULL;
}

Env* env_find(Env* env, const char* symbol) {
    Env* e = env->next;
    while (e) {
        if (e->symbol && strcmp(e->symbol->data.symbol, symbol) == 0) {
            return e;
        }
        e = e->next;
    }
    return NULL;
}

Value* env_get(Env* env, const char* symbol) {
    Env* found = env_find(env, symbol);
    if (found) {
        return found->value;
    }
    return make_nil();
}
