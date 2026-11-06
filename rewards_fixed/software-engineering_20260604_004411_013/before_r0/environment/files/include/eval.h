#ifndef EVAL_H
#define EVAL_H

#include "types.h"
#include "env.h"

Value* eval(Value* expr, Env* env);

#endif
