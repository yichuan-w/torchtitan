#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>

#define MAX_ROWS 100
#define MAX_COLS 26
#define MAX_FORMULA_LEN 256
#define MAX_CELLS (MAX_ROWS * MAX_COLS)

typedef enum {
    VAL_NUMBER,
    VAL_FORMULA,
    VAL_EMPTY
} CellType;

typedef struct {
    CellType type;
    double value;
    int evaluated;
    char formula[MAX_FORMULA_LEN];
} Cell;

static Cell grid[MAX_ROWS][MAX_COLS];
static int num_rows = 0, num_cols = 0;

/* Convert column letter to index: A=0, B=1, ..., Z=25 */
static int col_to_idx(char c) {
    return c - 'A' + 1;
}

/* Parse a cell reference like "A1", returns 0 on success, stores row/col */
static int parse_cell_ref(const char *s, int *len, int *row, int *col) {
    if (!isalpha(s[0])) return 0;
    *col = col_to_idx(toupper(s[0]));
    int pos = 1;
    if (*col < 0 || *col >= MAX_COLS) return 0;
    *row = 0;
    while (isdigit(s[pos])) {
        *row = *row * 10 + (s[pos] - '0');
        pos++;
    }
    *row -= 1; /* Convert from 1-based to 0-based */
    if (*row < 0 || *row >= MAX_ROWS) return 0;
    *len = pos;
    return 1;
}

/* Forward declaration */
static double eval_expr(const char *expr, int *pos, int depth);

/* Evaluate a term (handles numbers, cell refs, parenthesized expressions, unary minus) */
static double eval_term(const char *expr, int *pos, int depth) {
    /* Skip whitespace */
    while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;

    /* Unary minus */
    if (expr[*pos] == '-') {
        (*pos)++;
        double val = eval_term(expr, pos, depth);
        return -val;
    }

    /* Parenthesized expression */
    if (expr[*pos] == '(') {
        (*pos)++;
        double val = eval_expr(expr, pos, depth);
        if (expr[*pos] == ')') (*pos)++;
        return val;
    }

    /* Number */
    if (isdigit(expr[*pos]) || expr[*pos] == '.') {
        char *end;
        double val = strtod(&expr[*pos], &end);
        *pos = end - expr;
        return val;
    }

    /* Cell reference */
    if (isalpha(expr[*pos])) {
        int row, col, len;
        if (parse_cell_ref(&expr[*pos], &len, &row, &col)) {
            *pos += len;
            if (!grid[row][col].evaluated) {
                fprintf(stderr, "Error: circular reference detected at %c%d\n",
                        'A' + col, row + 1);
                exit(1);
            }
            return grid[row][col].value;
        }
    }

    /* Function: SUM(...) */
    if (strncmp(&expr[*pos], "SUM", 3) == 0) {
        *pos += 3;
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (expr[*pos] != '(') { fprintf(stderr, "Expected (\n"); exit(1); }
        (*pos)++;
        /* Parse range: e.g., A1:B3 */
        int r1, c1, r2, c2, len;
        if (!parse_cell_ref(&expr[*pos], &len, &r1, &c1)) {
            fprintf(stderr, "Expected cell ref\n"); exit(1);
        }
        *pos += len;
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (expr[*pos] != ':') { fprintf(stderr, "Expected :\n"); exit(1); }
        (*pos)++;
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (!parse_cell_ref(&expr[*pos], &len, &r2, &c2)) {
            fprintf(stderr, "Expected cell ref\n"); exit(1);
        }
        *pos += len;
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (expr[*pos] != ')') { fprintf(stderr, "Expected )\n"); exit(1); }
        (*pos)++;

        double sum = 0;
        for (int r = r1; r <= r2; r++) {
            for (int c = c1; c <= c2; c++) {
                if (!grid[r][c].evaluated) {
                    fprintf(stderr, "Error: circular reference in SUM range\n");
                    exit(1);
                }
                sum += grid[r][c].value;
            }
        }
        return sum;
    }

    fprintf(stderr, "Unexpected char '%c'\n", expr[*pos]);
    exit(1);
    return 0;
}

/* Evaluate multiplication/division */
static double eval_mul_div(const char *expr, int *pos, int depth) {
    double left = eval_term(expr, pos, depth);
    while (1) {
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (expr[*pos] == '*' || expr[*pos] == '/') {
            char op = expr[*pos];
            (*pos)++;
            double right = eval_term(expr, pos, depth);
            if (op == '*')
                left = left * right;
            else {
                if (right == 0) { fprintf(stderr, "Division by zero\n"); exit(1); }
                left = left / right;
            }
        } else {
            break;
        }
    }
    return left;
}

/* Evaluate addition/subtraction */
static double eval_add_sub(const char *expr, int *pos, int depth) {
    double left = eval_mul_div(expr, pos, depth);
    while (1) {
        while (expr[*pos] == ' ' || expr[*pos] == '\t') (*pos)++;
        if (expr[*pos] == '+' || expr[*pos] == '-') {
            char op = expr[*pos];
            (*pos)++;
            double right = eval_term(expr, pos, depth);
            if (op == '+')
                left = left + right;
            else
                left = left - right;
        } else {
            break;
        }
    }
    return left;
}

/* Top-level expression evaluation */
static double eval_expr(const char *expr, int *pos, int depth) {
    return eval_add_sub(expr, pos, depth);
}

/* Evaluate a formula (strips leading '=') */
static double evaluate_formula(const char *formula) {
    int pos = 0;
    double result = eval_expr(formula, &pos, 0);
    return result;
}

/* Detect circular references using DFS */
static int dfs_cycle(int r, int c, int *visiting, int *visited) {
    int idx = r * MAX_COLS + c;
    if (visiting[idx]) return 1;
    if (visited[idx]) return 0;
    if (grid[r][c].type != VAL_FORMULA) return 0;

    visiting[idx] = 1;

    /* Parse formula to find cell references */
    const char *f = grid[r][c].formula;
    int pos = 0;
    while (f[pos]) {
        if (isalpha(f[pos])) {
            int rr, cc, len;
            if (parse_cell_ref(&f[pos], &len, &rr, &cc)) {
                if (dfs_cycle(rr, cc, visiting, visited)) return 1;
                pos += len;
            } else {
                pos++;
            }
        } else {
            pos++;
        }
    }

    visiting[idx] = 0;
    visited[idx] = 1;
    return 0;
}

/* Build dependency order using topological sort */
static int topo_dfs(int r, int c, int *visiting, int *visited, int *order, int *order_idx) {
    int idx = r * MAX_COLS + c;
    if (visiting[idx]) return 0; /* Cycle */
    if (visited[idx]) return 1;
    if (grid[r][c].type != VAL_FORMULA) {
        visited[idx] = 1;
        order[(*order_idx)++] = idx;
        return 1;
    }

    visiting[idx] = 1;

    const char *f = grid[r][c].formula;
    int pos = 0;
    while (f[pos]) {
        if (isalpha(f[pos])) {
            int rr, cc, len;
            if (parse_cell_ref(&f[pos], &len, &rr, &cc)) {
                if (!topo_dfs(rr, cc, visiting, visited, order, order_idx)) return 0;
                pos += len;
            } else {
                pos++;
            }
        } else {
            pos++;
        }
    }

    visiting[idx] = 0;
    visited[idx] = 1;
    order[(*order_idx)++] = idx;
    return 1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <input.csv>\n", argv[0]);
        return 1;
    }

    FILE *fp = fopen(argv[1], "r");
    if (!fp) {
        fprintf(stderr, "Cannot open %s\n", argv[1]);
        return 1;
    }

    /* Read CSV */
    char line[4096];
    num_rows = 0;
    while (fgets(line, sizeof(line), fp) && num_rows < MAX_ROWS) {
        int col = 0;
        char *tok = strtok(line, ",\n");
        while (tok && col < MAX_COLS) {
            /* Trim whitespace */
            while (*tok == ' ' || *tok == '\t') tok++;
            if (*tok == '=') {
                grid[num_rows][col].type = VAL_FORMULA;
                strncpy(grid[num_rows][col].formula, tok + 1, MAX_FORMULA_LEN - 1);
                grid[num_rows][col].formula[MAX_FORMULA_LEN - 1] = '\0';
                grid[num_rows][col].value = 0;
                grid[num_rows][col].evaluated = 0;
            } else if (*tok == '\0') {
                grid[num_rows][col].type = VAL_EMPTY;
                grid[num_rows][col].value = 0;
                grid[num_rows][col].evaluated = 1;
            } else {
                grid[num_rows][col].type = VAL_NUMBER;
                grid[num_rows][col].value = atof(tok);
                grid[num_rows][col].evaluated = 1;
            }
            col++;
            tok = strtok(NULL, ",\n");
        }
        if (col > num_cols) num_cols = col;
        num_rows++;
    }
    fclose(fp);

    /* Check for circular references */
    int visiting[MAX_CELLS] = {0};
    int visited[MAX_CELLS] = {0};
    for (int r = 0; r < num_rows; r++) {
        for (int c = 0; c < num_cols; c++) {
            if (grid[r][c].type == VAL_FORMULA) {
                memset(visiting, 0, sizeof(visiting));
                memset(visited, 0, sizeof(visited));
                if (dfs_cycle(r, c, visiting, visited)) {
                    fprintf(stderr, "Error: circular reference detected\n");
                    return 1;
                }
            }
        }
    }

    /* Topological sort for evaluation order */
    int order[MAX_CELLS];
    int order_idx = 0;
    memset(visiting, 0, sizeof(visiting));
    memset(visited, 0, sizeof(visited));

    for (int r = 0; r < num_rows; r++) {
        for (int c = 0; c < num_cols; c++) {
            if (!topo_dfs(r, c, visiting, visited, order, &order_idx)) {
                fprintf(stderr, "Error: cycle in dependencies\n");
                return 1;
            }
        }
    }

    /* Evaluate in topological order */
    for (int i = 0; i < order_idx; i++) {
        int r = order[i] / MAX_COLS;
        int c = order[i] % MAX_COLS;
        if (grid[r][c].type == VAL_FORMULA) {
            grid[r][c].value = evaluate_formula(grid[r][c].formula);
            grid[r][c].evaluated = 1;
        }
    }

    /* Output results */
    for (int r = 0; r < num_rows; r++) {
        for (int c = 0; c < num_cols; c++) {
            if (c > 0) printf(",");
            if (grid[r][c].type == VAL_EMPTY) {
                printf("0");
            } else {
                /* Format to avoid trailing zeros for integers */
                if (grid[r][c].value == (long long)grid[r][c].value) {
                    printf("%lld", (long long)grid[r][c].value);
                } else {
                    printf("%.6g", grid[r][c].value);
                }
            }
        }
        printf("\n");
    }

    return 0;
}
