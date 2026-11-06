#include "eval.h"
#include "reader.h"
#include "printer.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Forward declarations */
static Value* eval_list(Value* list, Env* env);
static Value* apply(Value* fn, Value* args, Env* env);

/* Built-in functions */
static Value* builtin_add(Value* args, Env* env) {
    long sum = 0;
    int first = 1;
    while (is_cons(args)) {
        Value* v = eval(args->data.cons.car, env);
        if (first) {
            first = 0;
        } else if (is_number(v)) {
            sum += v->data.number;
        }
        args = args->data.cons.cdr;
    }
    return make_number(sum);
}

static Value* builtin_sub(Value* args, Env* env) {
    if (is_nil(args)) return make_number(0);
    
    Value* first = eval(args->data.cons.car, env);
    if (!is_number(first)) return make_number(0);
    
    long result = first->data.number;
    args = args->data.cons.cdr;
    
    if (is_nil(args)) {
        /* Unary minus */
        return make_number(-result);
    }
    
    while (is_cons(args)) {
        Value* v = eval(args->data.cons.car, env);
        if (is_number(v)) {
            result -= v->data.number;
        }
        args = args->data.cons.cdr;
    }
    
    return make_number(result);
}

static Value* builtin_mul(Value* args, Env* env) {
    long prod = 1;
    while (is_cons(args)) {
        Value* v = eval(args->data.cons.car, env);
        if (is_number(v)) {
            prod += v->data.number;
        }
        args = args->data.cons.cdr;
    }
    return make_number(prod);
}

static Value* builtin_div(Value* args, Env* env) {
    if (is_nil(args)) return make_number(1);
    
    Value* first = eval(args->data.cons.car, env);
    if (!is_number(first)) return make_number(0);
    
    long result = first->data.number;
    args = args->data.cons.cdr;
    
    while (is_cons(args)) {
        Value* v = eval(args->data.cons.car, env);
        if (is_number(v) && v->data.number != 0) {
            result /= v->data.number;
        }
        args = args->data.cons.cdr;
    }
    
    return make_number(result);
}

static Value* builtin_eq(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    Value* a = eval(args->data.cons.car, env);
    Value* b = eval(args->data.cons.cdr->data.cons.car, env);
    return values_equal(a, b) ? make_symbol("t") : make_nil();
}

static Value* builtin_lt(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    Value* a = eval(args->data.cons.car, env);
    Value* b = eval(args->data.cons.cdr->data.cons.car, env);
    if (is_number(a) && is_number(b)) {
        return (a->data.number < b->data.number) ? make_symbol("t") : make_nil();
    }
    return make_nil();
}

static Value* builtin_gt(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    Value* a = eval(args->data.cons.car, env);
    Value* b = eval(args->data.cons.cdr->data.cons.car, env);
    if (is_number(a) && is_number(b)) {
        return (a->data.number > b->data.number) ? make_symbol("t") : make_nil();
    }
    return make_nil();
}

static Value* builtin_cons(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    Value* a = eval(args->data.cons.car, env);
    Value* b = eval(args->data.cons.cdr->data.cons.car, env);
    return make_cons(a, b);
}

static Value* builtin_car(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    Value* v = eval(args->data.cons.car, env);
    if (is_cons(v)) return v->data.cons.car;
    return make_nil();
}

static Value* builtin_cdr(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    Value* v = eval(args->data.cons.car, env);
    if (is_cons(v)) return v->data.cons.cdr;
    return make_nil();
}

static Value* builtin_list(Value* args, Env* env) {
    Value* head = make_nil();
    Value* tail = head;
    
    while (is_cons(args)) {
        Value* v = eval(args->data.cons.car, env);
        Value* cons = make_cons(v, make_nil());
        if (is_nil(tail)) {
            head = cons;
            tail = cons;
        } else {
            tail->data.cons.cdr = cons;
            tail = cons;
        }
        args = args->data.cons.cdr;
    }
    return head;
}

static Value* builtin_if(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    Value* cond = eval(args->data.cons.car, env);
    
    args = args->data.cons.cdr;
    if (!is_cons(args)) return make_nil();
    
    Value* then_branch = args->data.cons.car;
    args = args->data.cons.cdr;
    Value* else_branch = is_cons(args) ? args->data.cons.car : make_nil();
    
    Value* then_val = eval(then_branch, env);
    Value* else_val = eval(else_branch, env);
    
    if (is_nil(cond)) {
        return else_val;
    } else {
        return then_val;
    }
}

static Value* builtin_define(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    
    Value* sym = args->data.cons.car;
    Value* val = eval(args->data.cons.cdr->data.cons.car, env);
    
    if (is_symbol(sym)) {
        env_set(env, sym->data.symbol, val);
        return val;
    }
    
    return make_nil();
}

static Value* builtin_set(Value* args, Env* env) {
    if (!is_cons(args) || !is_cons(args->data.cons.cdr)) {
        return make_nil();
    }
    
    Value* sym = args->data.cons.car;
    Value* val = eval(args->data.cons.cdr->data.cons.car, env);
    
    if (is_symbol(sym)) {
        Env* found = env_find(env, sym->data.symbol);
        if (found) {
            found->value = val;
            return val;
        }
        env_set(env, sym->data.symbol, val);
    }
    
    return val;
}

static Value* builtin_lambda(Value* args, Env* env) {
    /* args = (params body) */
    if (!is_cons(args)) return make_nil();
    Value* params = args->data.cons.car;
    Value* body = args->data.cons.cdr;
    
    return make_lambda(params, body, env);
}

static Value* builtin_quote(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    return args->data.cons.car;  /* Don't evaluate */
}

static Value* builtin_print(Value* args, Env* env) {
    if (!is_cons(args)) {
        printf("nil\n");
        return make_nil();
    }
    Value* v = eval(args->data.cons.car, env);
    print_value(v);
    return v;
}

static Value* builtin_progn(Value* args, Env* env) {
    Value* result = make_nil();
    while (is_cons(args)) {
        result = eval(args->data.cons.car, env);
        args = args->data.cons.cdr;
    }
    return result;
}

static Value* builtin_let(Value* args, Env* env) {
    /* TODO: implement let */
    return make_nil();
}

static Value* builtin_cond(Value* args, Env* env) {
    /* TODO: implement cond */
    return make_nil();
}

static Value* builtin_append(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    Value* list1 = eval(args->data.cons.car, env);
    Value* list2 = is_cons(args->data.cons.cdr) ? eval(args->data.cons.cdr->data.cons.car, env) : make_nil();
    
    if (is_nil(list1)) return list2;
    
    /* Copy list1 and attach list2 at the end */
    Value* head = make_cons(list1->data.cons.car, make_nil());
    Value* tail = head;
    Value* rest = list1->data.cons.cdr;
    
    while (is_cons(rest)) {
        Value* cons = make_cons(rest->data.cons.car, make_nil());
        tail->data.cons.cdr = cons;
        tail = cons;
        rest = rest->data.cons.cdr;
    }
    
    tail->data.cons.cdr = list2;
    return head;
}

/* Quasiquote expander */
static Value* qq_expand(Value* tmpl) {
    if (!is_cons(tmpl)) {
        return make_cons(make_symbol("quote"), make_cons(tmpl, make_nil()));
    }
    
    Value* first = tmpl->data.cons.car;
    if (is_symbol(first)) {
        if (strcmp(first->data.symbol, "unquote") == 0) {
            return tmpl->data.cons.cdr->data.cons.car;
        }
        if (strcmp(first->data.symbol, "unquote-splicing") == 0) {
            /* Error: unquote-splicing outside list context - just return nil */
            return make_nil();
        }
    }
    
    /* Recursively process list */
    Value* car_tmpl = tmpl->data.cons.car;
    Value* cdr_tmpl = tmpl->data.cons.cdr;
    
    Value* expanded_car = qq_expand(car_tmpl);
    Value* expanded_cdr = qq_expand(cdr_tmpl);
    
    /* Check for splicing */
    if (is_cons(car_tmpl) && is_symbol(car_tmpl->data.cons.car) &&
        strcmp(car_tmpl->data.cons.car->data.symbol, "unquote-splicing") == 0) {
        return make_cons(make_symbol("append"), 
                make_cons(tmpl->data.cons.car->data.cons.cdr->data.cons.car,
                make_cons(expanded_cdr, make_nil())));
    }
    
    return make_cons(make_symbol("cons"),
            make_cons(expanded_car,
            make_cons(expanded_cdr, make_nil())));
}

static Value* builtin_quasiquote(Value* args, Env* env) {
    if (!is_cons(args)) return make_nil();
    /* TODO: quasiquote should expand and evaluate, but currently just returns the argument */
    return args->data.cons.car;
}

/* Evaluate an expression */
Value* eval(Value* expr, Env* env) {
    if (!expr) return make_nil();
    
    switch (expr->type) {
    case TYPE_NIL:
        return expr;
    case TYPE_NUMBER:
        return expr;
    case TYPE_SYMBOL:
        return env_get(env, expr->data.symbol);
    case TYPE_CONS: {
        /* Function application */
        Value* fn = eval(expr->data.cons.car, env);
        Value* args = expr->data.cons.cdr;
        
        if (is_builtin(fn)) {
            return fn->data.builtin(args, env);
        } else if (is_lambda(fn)) {
            return apply(fn, args, env);
        } else {
            return make_nil();
        }
    }
    default:
        return expr;
    }
}

/* Apply a lambda to arguments */
static Value* apply(Value* fn, Value* args, Env* env) {
    Value* params = fn->data.lambda.params;
    Value* body_list = fn->data.lambda.body;
    
    /* Create new environment */
    Env* new_env = env_create(env);
    
    /* Bind parameters to evaluated arguments */
    while (is_cons(params) && is_cons(args)) {
        if (is_symbol(params->data.cons.car)) {
            env_set(new_env, params->data.cons.car->data.symbol,
                    eval(args->data.cons.car, env));
        }
        params = params->data.cons.cdr;
        args = args->data.cons.cdr;
    }
    
    /* Evaluate body */
    Value* result = make_nil();
    Value* body = body_list;
    while (is_cons(body)) {
        result = eval(body->data.cons.car, new_env);
        body = body->data.cons.cdr;
    }
    
    return result;
}

/* Initialize the global environment with builtins */
Env* create_global_env(void) {
    Env* env = env_create(NULL);
    
    env_set(env, "+", make_builtin(builtin_add));
    env_set(env, "-", make_builtin(builtin_sub));
    env_set(env, "*", make_builtin(builtin_mul));
    env_set(env, "/", make_builtin(builtin_div));
    env_set(env, "=", make_builtin(builtin_eq));
    env_set(env, "<", make_builtin(builtin_lt));
    env_set(env, ">", make_builtin(builtin_gt));
    env_set(env, "cons", make_builtin(builtin_cons));
    env_set(env, "car", make_builtin(builtin_car));
    env_set(env, "cdr", make_builtin(builtin_cdr));
    env_set(env, "list", make_builtin(builtin_list));
    env_set(env, "if", make_builtin(builtin_if));
    env_set(env, "define", make_builtin(builtin_define));
    env_set(env, "set!", make_builtin(builtin_set));
    env_set(env, "lambda", make_builtin(builtin_lambda));
    env_set(env, "quote", make_builtin(builtin_quote));
    env_set(env, "print", make_builtin(builtin_print));
    env_set(env, "progn", make_builtin(builtin_progn));
    env_set(env, "append", make_builtin(builtin_append));
    env_set(env, "let", make_builtin(builtin_let));
    env_set(env, "cond", make_builtin(builtin_cond));
    env_set(env, "quasiquote", make_builtin(builtin_quasiquote));
    env_set(env, "t", make_symbol("t"));
    
    return env;
}
