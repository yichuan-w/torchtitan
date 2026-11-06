#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef long long Integer;

typedef enum { EXPR_NUM, EXPR_VAR, EXPR_LIST } ExprType;

typedef struct Expr Expr;
struct Expr {
    ExprType type;
    Integer num;
    char *name;
    Expr **items;
    int count;
};

typedef enum { VAL_INT, VAL_FUNC } ValueType;

typedef struct Function Function;
typedef struct {
    ValueType type;
    Integer num;
    Function *func;
} Value;

typedef struct Binding Binding;
typedef struct Env Env;

struct Binding {
    char *name;
    Value value;
    Binding *next;
};

struct Env {
    Binding *head;
    Env *parent;
};

struct Function {
    char **params;
    int param_count;
    Expr **body;
    int body_count;
    Env *env;
};

static void fail(void) {
    fprintf(stderr, "Error\n");
    exit(1);
}

static void *xmalloc(size_t n) {
    void *p = malloc(n);
    if (!p) {
        fail();
    }
    return p;
}

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) {
        fail();
    }
    return q;
}

static char *xstrndup(const char *s, size_t n) {
    char *p = xmalloc(n + 1);
    memcpy(p, s, n);
    p[n] = '\0';
    return p;
}

static char *xstrdup(const char *s) {
    return xstrndup(s, strlen(s));
}

static Value make_int(Integer n) {
    Value v;
    v.type = VAL_INT;
    v.num = n;
    v.func = NULL;
    return v;
}

static Value make_func(Function *f) {
    Value v;
    v.type = VAL_FUNC;
    v.num = 0;
    v.func = f;
    return v;
}

static char *read_line(FILE *f) {
    size_t cap = 128;
    size_t len = 0;
    char *buf = xmalloc(cap);
    int c;

    while ((c = fgetc(f)) != EOF && c != '\n') {
        if (len + 1 >= cap) {
            cap *= 2;
            buf = xrealloc(buf, cap);
        }
        buf[len++] = (char)c;
    }

    if (c == EOF && len == 0) {
        free(buf);
        return NULL;
    }

    if (len > 0 && buf[len - 1] == '\r') {
        len--;
    }
    buf[len] = '\0';
    return buf;
}

typedef struct {
    const char *s;
    size_t pos;
} Parser;

static int is_delim(int c) {
    return c == '\0' || isspace((unsigned char)c) || c == '(' || c == ')';
}

static void skip_ws(Parser *p) {
    while (isspace((unsigned char)p->s[p->pos])) {
        p->pos++;
    }
}

static Expr *new_list(void) {
    Expr *e = xmalloc(sizeof(Expr));
    e->type = EXPR_LIST;
    e->num = 0;
    e->name = NULL;
    e->items = NULL;
    e->count = 0;
    return e;
}

static void list_append(Expr *list, Expr *item) {
    size_t n = (size_t)list->count + 1;
    list->items = xrealloc(list->items, sizeof(Expr *) * n);
    list->items[list->count] = item;
    list->count++;
}

static Expr *parse_expr(Parser *p);

static Expr *parse_list(Parser *p) {
    Expr *list = new_list();

    p->pos++;
    for (;;) {
        skip_ws(p);
        if (p->s[p->pos] == ')') {
            p->pos++;
            return list;
        }
        if (p->s[p->pos] == '\0') {
            fail();
        }
        list_append(list, parse_expr(p));
    }
}

static Expr *parse_atom(Parser *p) {
    const char *s = p->s;
    size_t start = p->pos;
    unsigned char c = (unsigned char)s[start];

    if (isdigit(c) || (c == '-' && isdigit((unsigned char)s[start + 1]))) {
        char *end;
        long long n;

        errno = 0;
        n = strtoll(s + start, &end, 10);
        if (errno == ERANGE || !is_delim((unsigned char)*end)) {
            fail();
        }

        p->pos = (size_t)(end - s);

        Expr *e = xmalloc(sizeof(Expr));
        e->type = EXPR_NUM;
        e->num = (Integer)n;
        e->name = NULL;
        e->items = NULL;
        e->count = 0;
        return e;
    }

    while (!is_delim((unsigned char)s[p->pos])) {
        p->pos++;
    }
    if (p->pos == start) {
        fail();
    }

    Expr *e = xmalloc(sizeof(Expr));
    e->type = EXPR_VAR;
    e->num = 0;
    e->name = xstrndup(s + start, p->pos - start);
    e->items = NULL;
    e->count = 0;
    return e;
}

static Expr *parse_expr(Parser *p) {
    skip_ws(p);
    if (p->s[p->pos] == '(') {
        return parse_list(p);
    }
    if (p->s[p->pos] == ')') {
        fail();
    }
    return parse_atom(p);
}

static Expr *parse_line(const char *line) {
    Parser p;
    Expr *e;

    p.s = line;
    p.pos = 0;

    skip_ws(&p);
    if (p.s[p.pos] == '\0') {
        fail();
    }

    e = parse_expr(&p);
    skip_ws(&p);
    if (p.s[p.pos] != '\0') {
        fail();
    }
    return e;
}

static Env *env_new(Env *parent) {
    Env *env = xmalloc(sizeof(Env));
    env->head = NULL;
    env->parent = parent;
    return env;
}

static Binding *env_find_binding(Env *env, const char *name) {
    Env *e;
    Binding *b;

    for (e = env; e != NULL; e = e->parent) {
        for (b = e->head; b != NULL; b = b->next) {
            if (strcmp(b->name, name) == 0) {
                return b;
            }
        }
    }
    return NULL;
}

static void env_add_binding(Env *env, const char *name, Value value) {
    Binding *b = xmalloc(sizeof(Binding));
    b->name = xstrdup(name);
    b->value = value;
    b->next = env->head;
    env->head = b;
}

static Value env_lookup(Env *env, const char *name) {
    Binding *b = env_find_binding(env, name);
    if (!b) {
        fail();
    }
    return b->value;
}

static void env_define(Env *env, const char *name, Value value) {
    Binding *b = env_find_binding(env, name);
    if (b) {
        b->value = value;
    } else {
        env_add_binding(env, name, value);
    }
}

static Value eval(Env *env, Expr *expr) {
    (void)env;
    (void)expr;
    fail();
    return make_int(0);
}

int main(void) {
    Env *global = env_new(NULL);
    char *line;
    char *buf = NULL;
    size_t cap = 0;
    size_t len = 0;
    int depth = 0;

    while ((line = read_line(stdin)) != NULL) {
        size_t line_len = strlen(line);
        size_t need = len + line_len + 2;
        size_t i;

        if (need > cap) {
            while (cap < need) {
                cap = cap ? cap * 2 : 128;
            }
            buf = xrealloc(buf, cap);
        }
        if (len > 0) {
            buf[len++] = ' ';
        }
        memcpy(buf + len, line, line_len);
        len += line_len;
        buf[len] = '\0';

        for (i = 0; i < line_len; i++) {
            if (line[i] == '(') {
                depth++;
            } else if (line[i] == ')') {
                depth--;
                if (depth < 0) {
                    fail();
                }
            }
        }
        free(line);

        if (depth == 0 && len > 0) {
            Expr *expr = parse_line(buf);
            Value value = eval(global, expr);
            printf(value.type == VAL_FUNC ? "0\n" : "%lld\n", value.num);
            len = 0;
            buf[0] = '\0';
        }
    }

    if (depth != 0 || len != 0) {
        fail();
    }
    free(buf);
    return 0;
}
