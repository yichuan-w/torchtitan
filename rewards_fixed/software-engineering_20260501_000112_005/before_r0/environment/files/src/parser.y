/* parser.y - Bison parser for a simple data format */
%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ast.h"

void yyerror(const char *s);
int yylex(void);
%}

%union {
    char *str;
    struct ast_node *node;
    struct ast_list *list;
}

%token <str> TOKEN_NUMBER TOKEN_STRING TOKEN_IDENTIFIER
%token TOKEN_LBRACE TOKEN_RBRACE TOKEN_LBRACKET TOKEN_RBRACKET
%token TOKEN_COLON TOKEN_COMMA TOKEN_ASSIGN
%token TOKEN_TRUE TOKEN_FALSE TOKEN_NULL

%type <node> value object member array
%type <list> members values

%start document

%%

document: value { root_node = $1; }

value: TOKEN_NUMBER      { $$ = ast_new_number($1); free($1); }
     | TOKEN_STRING      { $$ = ast_new_string($1); free($1); }
     | TOKEN_TRUE        { $$ = ast_new_bool(1); }
     | TOKEN_FALSE       { $$ = ast_new_bool(0); }
     | TOKEN_NULL        { $$ = ast_new_null(); }
     | object            { $$ = $1; }
     | array             { $$ = $1; }

object: TOKEN_LBRACE members TOKEN_RBRACE { $$ = ast_new_object($2); }

members: /* empty */   { $$ = NULL; }
       | member        { $$ = ast_list_new($1, NULL); }
       | members TOKEN_COMMA member { $$ = ast_list_append($1, $3); }

member: TOKEN_IDENTIFIER TOKEN_COLON value { $$ = ast_new_member($1, $3); free($1); }

array: TOKEN_LBRACKET values TOKEN_RBRACKET { $$ = ast_new_array($2); }

values: /* empty */   { $$ = NULL; }
      | value         { $$ = ast_list_new($1, NULL); }
      | values TOKEN_COMMA value { $$ = ast_list_append($1, $3); }

%%

void yyerror(const char *s) {
    fprintf(stderr, "Parse error: %s\n", s);
}

struct ast_node *root_node = NULL;
