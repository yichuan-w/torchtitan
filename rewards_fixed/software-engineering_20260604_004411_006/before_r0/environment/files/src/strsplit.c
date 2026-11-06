#include "strlib.h"
#include <stdlib.h>
#include <string.h>

char **strsplit(const char *s, char delim, size_t *count) {
    if (!s || !count) return NULL;
    size_t n = 1;
    for (const char *p = s; *p; p++)
        if (*p == delim) n++;
    char **result = malloc((n + 1) * sizeof(char *));
    if (!result) return NULL;
    size_t idx = 0;
    const char *p = s;
    while (*p) {
        if (*p == delim) { p++; continue; }
        const char *start = p;
        while (*p && *p != delim) p++;
        size_t len = p - start;
        result[idx] = malloc(len + 1);
        if (result[idx]) { memcpy(result[idx], start, len); result[idx][len] = '\0'; }
        idx++;
    }
    result[idx] = NULL;
    *count = idx;
    return result;
}

void strsplit_free(char **parts) {
    if (!parts) return;
    for (size_t i = 0; parts[i]; i++) free(parts[i]);
    free(parts);
}
