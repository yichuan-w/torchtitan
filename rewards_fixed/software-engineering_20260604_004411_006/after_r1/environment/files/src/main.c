#include "strlib.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "Usage: %s <string> <delimiter>\n", argv[0]); return 1; }
    strbuf *sb = strbuf_new();
    for (const char *p = argv[1]; *p; p++) strbuf_append_char(sb, *p);
    printf("Buffer: [%s]\n", strbuf_cstr(sb));
    size_t count;
    char **parts = strsplit(strbuf_cstr(sb), argv[2][0], &count);
    printf("Parts (%zu):\n", count);
    for (size_t i = 0; i < count; i++) printf("  [%zu]: '%s'\n", i, parts[i]?parts[i]:"NULL");
    strsplit_free(parts); strbuf_free(sb);
    return 0;
}
