/* utils.h - Utility functions */
#ifndef UTILS_H
#define UTILS_H

#include <stddef.h>

char *util_strdup(const char *s);
void *util_malloc(size_t size);
void util_free(void *ptr);
int util_parse_number(const char *s);
char *util_escape_string(const char *s);

#endif /* UTILS_H */
