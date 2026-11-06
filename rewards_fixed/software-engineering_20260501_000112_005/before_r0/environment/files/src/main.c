/* main.c - Main program for the parser */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"
#include "ast.h"

extern int yyparse(void);
extern FILE *yyin;
extern int lexer_get_line(void);

static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    
    char *buf = malloc(size + 1);
    if (!buf) { fclose(f); return NULL; }
    
    size_t read = fread(buf, 1, size, f);
    buf[read] = '\0';
    fclose(f);
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <input-file>\n", argv[0]);
        return 1;
    }
    
    yyin = fopen(argv[1], "r");
    if (!yyin) {
        fprintf(stderr, "Error: Cannot open input file '%s'\n", argv[1]);
        return 1;
    }
    
    printf("Parser v%d.%d.%d\n", VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH);
    printf("Parsing: %s\n\n", argv[1]);
    
    if (yyparse() != 0) {
        fprintf(stderr, "Parse failed at line %d\n", lexer_get_line());
        fclose(yyin);
        return 1;
    }
    
    fclose(yyin);
    
    if (root_node) {
        printf("Parse successful!\n\n");
        printf("AST:\n");
        ast_print(root_node, 0);
        ast_free(root_node);
    }
    
    return 0;
}
