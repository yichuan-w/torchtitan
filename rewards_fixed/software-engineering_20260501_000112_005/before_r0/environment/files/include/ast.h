/* ast.h - Abstract Syntax Tree structures */
#ifndef AST_H
#define AST_H

enum ast_type {
    AST_NUMBER,
    AST_STRING,
    AST_BOOL,
    AST_NULL,
    AST_OBJECT,
    AST_ARRAY,
    AST_MEMBER
};

struct ast_node {
    enum ast_type type;
    union {
        char *number_val;
        char *string_val;
        int bool_val;
        struct {
            char *key;
            struct ast_node *value;
        } member;
        struct ast_list *children;
    } data;
};

struct ast_list {
    struct ast_node *node;
    struct ast_list *next;
};

struct ast_node *ast_new_number(const char *val);
struct ast_node *ast_new_string(const char *val);
struct ast_node *ast_new_bool(int val);
struct ast_node *ast_new_null(void);
struct ast_node *ast_new_object(struct ast_list *members);
struct ast_node *ast_new_array(struct ast_list *values);
struct ast_node *ast_new_member(const char *key, struct ast_node *value);

struct ast_list *ast_list_new(struct ast_node *node, struct ast_list *next);
struct ast_list *ast_list_append(struct ast_list *list, struct ast_node *node);

void ast_print(struct ast_node *node, int indent);
void ast_free(struct ast_node *node);

extern struct ast_node *root_node;

#endif /* AST_H */
