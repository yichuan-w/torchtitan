#include "types.h"
#include "reader.h"
#include "printer.h"
#include "eval.h"
#include "env.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern Env* create_global_env(void);

int main(int argc, char** argv) {
    Env* global_env = create_global_env();
    
    if (argc > 1) {
        /* Execute file */
        FILE* f = fopen(argv[1], "r");
        if (!f) {
            fprintf(stderr, "Cannot open file: %s\n", argv[1]);
            return 1;
        }
        
        fseek(f, 0, SEEK_END);
        long size = ftell(f);
        fseek(f, 0, SEEK_SET);
        
        char* buf = malloc(size + 1);
        fread(buf, 1, size, f);
        buf[size] = '\0';
        fclose(f);
        
        /* Simple evaluator: read and eval each top-level expression */
        char* p = buf;
        int expr_count = 0;
        while (*p) {
            /* Skip whitespace */
            while (*p == ' ' || *p == '\n' || *p == '\t' || *p == '\r') p++;
            if (*p == '\0') break;
            
            /* Find the matching close paren */
            if (*p == '(') {
                int depth = 0;
                char* start = p;
                while (*p) {
                    if (*p == '(') depth++;
                    if (*p == ')') depth--;
                    if (depth == 0) {
                        p++;
                        break;
                    }
                    p++;
                }
                /* Extract expression */
                int len = p - start;
                char* expr_str = malloc(len + 1);
                strncpy(expr_str, start, len);
                expr_str[len] = '\0';
                
                Value* expr = read_from_string(expr_str);
                Value* result = eval(expr, global_env);
                /* Print result */
                if (expr->type == TYPE_CONS && 
                    expr->data.cons.car->type == TYPE_SYMBOL) {
                    print_value(result);
                }
                
                free(expr_str);
            } else {
                /* Atom - read it */
                char* start = p;
                while (*p && *p != ' ' && *p != '\n' && *p != '\t' && *p != '\r' && *p != '(' && *p != ')') p++;
                int len = p - start;
                char* expr_str = malloc(len + 1);
                strncpy(expr_str, start, len);
                expr_str[len] = '\0';
                
                Value* expr = read_from_string(expr_str);
                Value* result = eval(expr, global_env);
                print_value(result);
                
                free(expr_str);
            }
            expr_count++;
        }
        
        free(buf);
    } else {
        /* REPL */
        printf("MiniLisp REPL. Type 'quit' to exit.\n");
        char line[1024];
        
        while (1) {
            printf("> ");
            fflush(stdout);
            if (!fgets(line, sizeof(line), stdin)) break;
            
            /* Remove newline */
            line[strcspn(line, "\n")] = '\0';
            
            if (strcmp(line, "quit") == 0) break;
            
            Value* expr = read_from_string(line);
            Value* result = eval(expr, global_env);
            print_value(result);
        }
    }
    
    return 0;
}
