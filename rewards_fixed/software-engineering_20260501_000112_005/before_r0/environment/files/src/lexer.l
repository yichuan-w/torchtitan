/* lexer.l - Flex lexer definition for a simple tokenizer */
%{
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parser.h"
#include "config.h"

int line_num = 1;
%}

%option noyywrap

DIGIT    [0-9]
ID       [a-zA-Z_][a-zA-Z0-9_]*
STRING   \"([^\"\n]|\\.)*\"

%%

"{"            { return TOKEN_LBRACE; }
"}"            { return TOKEN_RBRACE; }
"["            { return TOKEN_LBRACKET; }
"]"            { return TOKEN_RBRACKET; }
":"            { return TOKEN_COLON; }
","            { return TOKEN_COMMA; }
"="            { return TOKEN_ASSIGN; }

"true"         { return TOKEN_TRUE; }
"false"        { return TOKEN_FALSE; }
"null"         { return TOKEN_NULL; }

{DIGIT}+       { yylval.str = strdup(yytext); return TOKEN_NUMBER; }
{STRING}       { yylval.str = strdup(yytext); return TOKEN_STRING; }
{ID}           { yylval.str = strdup(yytext); return TOKEN_IDENTIFIER; }

[ \t\r]+       { /* skip whitespace */ }
\n             { line_num++; }

"//"           { /* skip line comments */
                 int c;
                 while ((c = input()) != '\n' && c != EOF);
                 if (c == '\n') line_num++;
               }

"/*"           { /* skip block comments */
                 int c1 = 0, c2 = input();
                 while (c1 != '*' || c2 != '/') {
                   if (c2 == '\n') line_num++;
                   if (c2 == EOF) break;
                   c1 = c2;
                   c2 = input();
                 }
               }

.              { fprintf(stderr, "Unexpected character at line %d: %s\n", line_num, yytext); }

%%

int lexer_get_line(void) {
    return line_num;
}
