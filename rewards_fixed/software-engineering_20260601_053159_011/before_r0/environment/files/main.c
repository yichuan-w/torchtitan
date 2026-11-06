#include "kvdb.h"
#include "kvdb_config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_LINE 1024

static void print_usage(void) {
    printf("Commands:\n");
    printf("  PUT <key> <value>  - store a key-value pair\n");
    printf("  GET <key>          - retrieve a value\n");
    printf("  DEL <key>          - delete a key\n");
    printf("  COUNT              - show number of entries\n");
    printf("  DUMP               - dump all entries\n");
    printf("  HELP               - show this help\n");
    printf("  EXIT               - exit the program\n");
}

int main(int argc, char **argv) {
    kvdb_t *db;
    char line[MAX_LINE];
    char cmd[32];
    char key[KVDB_MAX_KEY_LEN];
    char val[KVDB_MAX_VAL_LEN];

    /* Check for batch mode: read commands from stdin */
    if (argc > 1 && strcmp(argv[1], "--batch") == 0) {
        db = kvdb_create();
        if (!db) {
            fprintf(stderr, "Failed to create database\n");
            return 1;
        }

        while (fgets(line, sizeof(line), stdin)) {
            /* Remove newline */
            line[strcspn(line, "\n")] = '\0';

            if (sscanf(line, "%31s", cmd) != 1) continue;

            if (strcmp(cmd, "PUT") == 0) {
                char kbuf[KVDB_MAX_KEY_LEN] = {0};
                char vbuf[KVDB_MAX_VAL_LEN] = {0};
                if (sscanf(line, "%*s %64s %256s", kbuf, vbuf) == 2) {
                    kvdb_put(db, kbuf, vbuf);
                }
            } else if (strcmp(cmd, "GET") == 0) {
                char kbuf[KVDB_MAX_KEY_LEN] = {0};
                if (sscanf(line, "%*s %64s", kbuf) == 1) {
                    const char *v = kvdb_get(db, kbuf);
                    if (v) {
                        printf("%s\n", v);
                    } else {
                        printf("NOTFOUND\n");
                    }
                }
            } else if (strcmp(cmd, "DEL") == 0) {
                char kbuf[KVDB_MAX_KEY_LEN] = {0};
                if (sscanf(line, "%*s %64s", kbuf) == 1) {
                    if (kvdb_del(db, kbuf) != 0) {
                    }
                }
            } else if (strcmp(cmd, "COUNT") == 0) {
                printf("%zu\n", kvdb_count(db));
            } else if (strcmp(cmd, "DUMP") == 0) {
                kvdb_dump(db);
            } else if (strcmp(cmd, "EXIT") == 0) {
                break;
            }
        }

        kvdb_destroy(db);
        return 0;
    }

    /* Interactive mode */
    printf("kvdb - Simple Key-Value Database\n");
    printf("Type HELP for commands, EXIT to quit.\n\n");

    db = kvdb_create();
    if (!db) {
        fprintf(stderr, "Failed to create database\n");
        return 1;
    }

    while (1) {
        printf("kvdb> ");
        fflush(stdout);

        if (!fgets(line, sizeof(line), stdin)) break;
        line[strcspn(line, "\n")] = '\0';

        if (sscanf(line, "%31s", cmd) != 1) continue;

        if (strcmp(cmd, "PUT") == 0) {
            if (sscanf(line, "%*s %64s %256s", key, val) == 2) {
                if (kvdb_put(db, key, val) != 0) {
                    printf("ERROR: put failed\n");
                } else {
                    printf("OK\n");
                }
            } else {
                printf("Usage: PUT <key> <value>\n");
            }
        } else if (strcmp(cmd, "GET") == 0) {
            if (sscanf(line, "%*s %64s", key) == 1) {
                const char *v = kvdb_get(db, key);
                if (v) {
                    printf("%s\n", v);
                } else {
                    printf("NOTFOUND\n");
                }
            } else {
                printf("Usage: GET <key>\n");
            }
        } else if (strcmp(cmd, "DEL") == 0) {
            if (sscanf(line, "%*s %64s", key) == 1) {
                if (kvdb_del(db, key) != 0) {
                } else {
                    printf("OK\n");
                }
            } else {
                printf("Usage: DEL <key>\n");
            }
        } else if (strcmp(cmd, "COUNT") == 0) {
            printf("%zu\n", kvdb_count(db));
        } else if (strcmp(cmd, "DUMP") == 0) {
            kvdb_dump(db);
        } else if (strcmp(cmd, "HELP") == 0) {
            print_usage();
        } else if (strcmp(cmd, "EXIT") == 0 || strcmp(cmd, "QUIT") == 0) {
            break;
        } else if (strlen(cmd) > 0) {
            printf("Unknown command: %s\n", cmd);
        }
    }

    kvdb_destroy(db);
    return 0;
}
