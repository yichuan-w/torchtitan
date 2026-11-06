#ifndef STRLIB_H
#define STRLIB_H
#include <stddef.h>
typedef struct { char *data; size_t len; size_t cap; } strbuf;
strbuf *strbuf_new(void);
void strbuf_free(strbuf *sb);
void strbuf_append(strbuf *sb, const char *s);
void strbuf_append_char(strbuf *sb, char c);
const char *strbuf_cstr(strbuf *sb);
char **strsplit(const char *s, char delim, size_t *count);
void strsplit_free(char **parts);
#endif
