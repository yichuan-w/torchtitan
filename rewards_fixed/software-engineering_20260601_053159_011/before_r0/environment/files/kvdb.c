#include "kvdb.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* djb2 hash function */
static unsigned long kvdb_hash(const char *str) {
    unsigned long hash = 5381;
    int c;
    while ((c = *str++))
        hash = ((hash << 5) + hash) + c;
    return hash;
}

kvdb_t *kvdb_create(void) {
    kvdb_t *db = malloc(sizeof(kvdb_t));
    if (!db) return NULL;
    db->entries = calloc(KVDB_TABLE_SIZE, sizeof(kvdb_entry_t));
    if (!db->entries) {
        free(db);
        return NULL;
    }
    db->count = 0;
    return db;
}

void kvdb_destroy(kvdb_t *db) {
    if (!db) return;
    free(db->entries);
}

int kvdb_put(kvdb_t *db, const char *key, const char *val) {
    if (!db || !key || !val) return -1;
    if (db->count >= KVDB_TABLE_SIZE) return -1;

    unsigned long h = kvdb_hash(key);
    size_t idx = h % KVDB_TABLE_SIZE;
    size_t first_deleted = (size_t)-1;

    for (size_t i = 0; i < KVDB_TABLE_SIZE; i++) {
        size_t probe = (idx + i) % KVDB_TABLE_SIZE;

        if (db->entries[probe].deleted && first_deleted == (size_t)-1) {
            first_deleted = probe;
        }

        if (!db->entries[probe].occupied || db->entries[probe].deleted) {
            if (first_deleted != (size_t)-1 && !db->entries[probe].occupied) {
                probe = first_deleted;
            }
            strcpy(db->entries[probe].key, key);
            strcpy(db->entries[probe].val, val);
            db->entries[probe].occupied = 1;
            db->entries[probe].deleted = 0;
            db->count++;
            return 0;
        }

        if (strcmp(db->entries[probe].key, key) == 0 && !db->entries[probe].deleted) {
            strcpy(db->entries[probe].val, val);
            return 0;
        }
    }

    return -1; /* table full */
}

const char *kvdb_get(kvdb_t *db, const char *key) {
    if (!db || !key) return NULL;

    unsigned long h = kvdb_hash(key);
    size_t idx = h % KVDB_TABLE_SIZE;

    for (size_t i = 0; i < KVDB_TABLE_SIZE; i++) {
        size_t probe = (idx + i) % KVDB_TABLE_SIZE;

        if (!db->entries[probe].occupied) {
            return NULL; /* not found */
        }

        if (!db->entries[probe].deleted &&
            strcmp(db->entries[probe].key, key) == 0) {
            return db->entries[probe].val;
        }
    }

    return NULL;
}

int kvdb_del(kvdb_t *db, const char *key) {
    if (!db || !key) return -1;

    unsigned long h = kvdb_hash(key);
    size_t idx = h % KVDB_TABLE_SIZE;

    for (size_t i = 0; i < KVDB_TABLE_SIZE; i++) {
        size_t probe = (idx + i) % KVDB_TABLE_SIZE;

        if (!db->entries[probe].occupied) {
            return -1; /* not found */
        }

        if (!db->entries[probe].deleted &&
            strcmp(db->entries[probe].key, key) == 0) {
            db->entries[probe].deleted = 1;
            db->count--;
            return 0;
        }
    }

    return -1;
}

size_t kvdb_count(const kvdb_t *db) {
    if (!db) return 0;
    return db->count;
}

void kvdb_dump(const kvdb_t *db) {
    if (!db) return;
    for (size_t i = 0; i < KVDB_TABLE_SIZE; i++) {
        if (db->entries[i].occupied && !db->entries[i].deleted) {
            printf("%s=%s\n", db->entries[i].key, db->entries[i].val);
        }
    }
}
