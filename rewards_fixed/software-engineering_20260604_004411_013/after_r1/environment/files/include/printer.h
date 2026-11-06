#ifndef PRINTER_H
#define PRINTER_H

#include "types.h"

void print_value(Value* v);
void print_value_to_buf(Value* v, char* buf, int* pos);

#endif
