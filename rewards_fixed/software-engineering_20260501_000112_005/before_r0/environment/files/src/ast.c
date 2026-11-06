/* ast.c - Abstract Syntax Tree implementation */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ast.h"

struct ast_node *ast_new_number(const char *val) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_NUMBER;
    node->data.number_val = strdup(val);
    return node;
}

struct ast_node *ast_new_string(const char *val) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_STRING;
    node->data.string_val = strdup(val);
    return node;
}

struct ast_node *ast_new_bool(int val) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_BOOL;
    node->data.bool_val = val;
    return node;
}

struct ast_node *ast_new_null(void) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_NULL;
    return node;
}

struct ast_node *ast_new_object(struct ast_list *members) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_OBJECT;
    node->data.children = members;
    return node;
}

struct ast_node *ast_new_array(struct ast_list *values) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_ARRAY;
    node->data.children = values;
    return node;
}

struct ast_node *ast_new_member(const char *key, struct ast_node *value) {
    struct ast_node *node = malloc(sizeof(struct ast_node));
    node->type = AST_MEMBER;
    node->data.member.key = strdup(key);
    node->data.member.value = value;
    return node;
}

struct ast_list *ast_list_new(struct ast_node *node, struct ast_list *next) {
    struct ast_list *list = malloc(sizeof(struct ast_list));
    list->node = node;
    list->next = next;
    return list;
}

struct ast_list *ast_list_append(struct ast_list *list, struct ast_node *node) {
    if (!list) return ast_list_new(node, NULL);
    struct ast_list *curr = list;
    while (curr->next) curr = curr->next;
    curr->next = ast_list_new(node, NULL);
    return list;
}

static void print_indent(int indent) {
    for (int i = 0; i < indent; i++) printf("  ");
}

void ast_print(struct ast_node *node, int indent) {
    if (!node) return;
    
    print_indent(indent);
    
    switch (node->type) {
        case AST_NUMBER:
            printf("NUMBER: %s\n", node->data.number_val);
            break;
        case AST_STRING:
            printf("STRING: %s\n", node->data.string_val);
            break;
        case AST_BOOL:
            printf("BOOL: %s\n", node->data.bool_val ? "true" : "false");
            break;
        case AST_NULL:
            printf("NULL\n");
            break;
        case AST_OBJECT:
            printf("OBJECT:\n");
            for (struct ast_list *m = node->data.children; m; m = m->next) {
                ast_print(m->node, indent + 1);
            }
            break;
        case AST_ARRAY:
            printf("ARRAY:\n");
            for (struct ast_list *v = node->data.children; v; v = v->next) {
                ast_print(v->node, indent + 1);
            }
            break;
        case AST_MEMBER:
            printf("MEMBER: %s =\n", node->data.member.key);
            ast_print(node->data.member.value, indent + 1);
            break;
    }
}

void ast_free(struct ast_node *node) {
    if (!node) return;
    
    switch (node->type) {
        case AST_NUMBER:
            free(node->data.number_val);
            break;
        case AST_STRING:
            free(node->data.string_val);
            break;
        case AST_MEMBER:
            free(node->data.member.key);
            ast_free(node->data.member.value);
            break;
        case AST_OBJECT:
        case AST_ARRAY:
            for (struct ast_list *curr = node->data.children; curr; ) {
                struct ast_list *next = curr->next;
                ast_free(curr->node);
                free(curr);
                curr = next;
            }
            break;
        default:
            break;
    }
    free(node);
}
