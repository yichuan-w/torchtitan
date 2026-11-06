#include "reader.h"
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdio.h>

typedef struct {
    const char* input;
    int pos;
} ReaderState;

static void skip_whitespace(ReaderState* rs) {
    while (rs->input[rs->pos] == ' ') {
        rs->pos++;
    }
}

static int is_delimiter(char c) {
    return c == '(' || c == ')' || c == ' ' || c == '\t' || c == '\n' || c == '\0';
}

static Value* read_atom(ReaderState* rs);

static Value* read_list(ReaderState* rs) {
    rs->pos++; /* skip '(' */
    skip_whitespace(rs);
    
    Value* head = make_nil();
    Value* tail = head;
    
    while (rs->input[rs->pos] != ')') {
        if (rs->input[rs->pos] == '\0') {
            fprintf(stderr, "Warning: unexpected end of input\n");
            return head->data.cons.cdr ? head->data.cons.cdr : make_nil();
        }
        
        Value* val;
        if (rs->input[rs->pos] == '(') {
            val = read_list(rs);
        } else {
            val = read_atom(rs);
        }
        
        Value* cons = make_cons(val, make_nil());
        if (is_nil(tail)) {
            head = cons;
            tail = cons;
        } else {
            tail->data.cons.cdr = cons;
            tail = cons;
        }
        
        skip_whitespace(rs);
    }
    
    rs->pos++; /* skip ')' */
    return head;
}

static Value* read_number(ReaderState* rs) {
    long val = 0;
    
    if (rs->input[rs->pos] == '-') {
        rs->pos++;
    }
    
    while (rs->input[rs->pos] >= '0' && rs->input[rs->pos] <= '9') {
        val = val * 10 + (rs->input[rs->pos] - '0');
        rs->pos++;
    }
    
    return make_number(val);
}

static Value* read_symbol(ReaderState* rs) {
    char buf[256];
    int i = 0;
    
    while (!is_delimiter(rs->input[rs->pos]) && i < 255) {
        buf[i] = rs->input[rs->pos];
        rs->pos++;
        i++;
    }
    buf[i] = '\0';
    
    if (strcmp(buf, "nil") == 0) return make_nil();
    
    return make_symbol(buf);
}

static Value* read_atom(ReaderState* rs) {
    char c = rs->input[rs->pos];
    
    if (c == '(') {
        return read_list(rs);
    } else if (c == '\'') {
        rs->pos++; /* skip quote */
        Value* quoted = read_atom(rs);
        return make_cons(make_symbol("quote"), make_cons(quoted, make_nil()));
    } else if (c == '`') {
        rs->pos++; /* skip backquote */
        Value* quoted = read_atom(rs);
        return make_cons(make_symbol("quasiquote"), make_cons(quoted, make_nil()));
    } else if (c == ',') {
        rs->pos++;
        if (rs->input[rs->pos] == '@') {
            rs->pos++;
            Value* quoted = read_atom(rs);
            return make_cons(make_symbol("unquote-splicing"), make_cons(quoted, make_nil()));
        }
        Value* quoted = read_atom(rs);
        return make_cons(make_symbol("unquote"), make_cons(quoted, make_nil()));
    } else if (c == '-' || (c >= '0' && c <= '9')) {
        return read_number(rs);
    } else if (c == ')') {
        rs->pos++;
        return make_nil();
    } else {
        return read_symbol(rs);
    }
}

Value* read_from_string(const char* input) {
    ReaderState rs;
    rs.input = input;
    rs.pos = 0;
    
    skip_whitespace(&rs);
    
    if (rs.input[rs.pos] == '\0') {
        return make_nil();
    }
    
    Value* result = read_atom(&rs);
    
    return result;
}
