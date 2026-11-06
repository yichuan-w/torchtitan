/* utils.c - Utility functions implementation */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "utils.h"

char *util_strdup(const char *s) {
    if (!s) return NULL;
    size_t len = strlen(s) + 1;
    char *dup = malloc(len);
    if (dup) memcpy(dup, s, len);
    return dup;
}

void *util_malloc(size_t size) {
    void *ptr = malloc(size);
    if (!ptr && size > 0) {
        fprintf(stderr, "Memory allocation failed for size %zu\n", size);
        exit(1);
    }
    return ptr;
}

void util_free(void *ptr) {
    if (ptr) free(ptr);
}

int util_parse_number(const char *s) {
    if (!s) return 0;
    int val = 0;
    int sign = 1;
    
    if (*s == '-') {
        sign = -1;
        s++;
    } else if (*s == '+') {
        s++;
    }
    
    while (*s >= '0' && *s <= '9') {
        val = val * 10 + (*s - '0');
        s++;
    }
    
    return sign * val;
}

char *util_escape_string(const char *s) {
    if (!s) return NULL;
    
    size_t len = strlen(s);
    char *escaped = malloc(len * 2 + 1);
    if (!escaped) return NULL;
    
    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        switch (s[i]) {
            case '\n': escaped[j++] = '\\'; escaped[j++] = 'n'; break;
            case '\t': escaped[j++] = '\\'; escaped[j++] = 't'; break;
            case '\r': escaped[j++] = '\\'; escaped[j++] = 'r'; break;
            case '\\': escaped[j++] = '\\'; escaped[j++] = '\\'; break;
            case '"':  escaped[j++] = '\\'; escaped[j++] = '"'; break;
            default: escaped[j++] = s[i]; break;
        }
    }
    escaped[j] = '\0';
    return escaped;
}
