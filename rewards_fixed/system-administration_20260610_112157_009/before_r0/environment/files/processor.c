#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define CONFIG_PATH "/app/config/processor.cfg"
#define DATA_DIR    "/app/data"

int main(int argc, char **argv) {
    FILE *cfg;
    char line[256];
    char prefix[64] = "OUT: ";
    int maxlen = 1024;
    struct stat st;

    /* 1. Read config file */
    cfg = fopen(CONFIG_PATH, "r");
    if (!cfg) {
        return 1;
    }
    while (fgets(line, sizeof(line), cfg)) {
        line[strcspn(line, "\r\n")] = 0;
        if (strncmp(line, "prefix=", 7) == 0) {
            strncpy(prefix, line + 7, sizeof(prefix) - 1);
            prefix[sizeof(prefix) - 1] = '\0';
        } else if (strncmp(line, "maxlen=", 7) == 0) {
            maxlen = atoi(line + 7);
            if (maxlen < 1) maxlen = 1024;
        }
    }
    fclose(cfg);

    /* 2. Ensure data directory exists */
    if (stat(DATA_DIR, &st) != 0) {
        return 2;
    }
    if (!S_ISDIR(st.st_mode)) {
        return 2;
    }

    /* 3. Process stdin to stdout */
    char *buf = (char *)malloc(maxlen + 1);
    if (!buf) {
        return 3;
    }

    while (fgets(buf, maxlen + 1, stdin)) {
        fprintf(stdout, "%s%s", prefix, buf);
    }

    free(buf);
    return 0;
}