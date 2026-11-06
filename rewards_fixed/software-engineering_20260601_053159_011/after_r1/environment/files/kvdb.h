#ifndef KVDB_H
#define KVDB_H

#include <stddef.h>
#include "kvdb_config.h"

#define KVDB_MAX_KEY_LEN 64
#define KVDB_MAX_VAL_LEN 256

#ifndef KVDB_TABLE_SIZE
#define KVDB_TABLE_SIZE KVDB_MAX_TABLE_SIZE
#endif

typedef struct kvdb_entry {
    char key[KVDB_MAX_KEY_LEN];
    char val[KVDB_MAX_VAL_LEN];
    int occupied;
    int deleted;
} kvdb_entry_t;

typedef struct {
    kvdb_entry_t *entries;
    size_t count;
} kvdb_t;

/* Create a new database. Returns NULL on failure. */
kvdb_t *kvdb_create(void);

/* Destroy the database and free all resources. */
void kvdb_destroy(kvdb_t *db);

/* Insert or update a key-value pair. Returns 0 on success, -1 on error. */
int kvdb_put(kvdb_t *db, const char *key, const char *val);

/* Retrieve a value by key. Returns a pointer to the value, or NULL if not found.
   The returned pointer is valid until the next kvdb_put or kvdb_del. */
const char *kvdb_get(kvdb_t *db, const char *key);

/* Delete a key. Returns 0 on success, -1 if not found. */
int kvdb_del(kvdb_t *db, const char *key);

/* Return the number of entries in the database. */
size_t kvdb_count(const kvdb_t *db);

/* Dump all entries to stdout in "key=value" format, one per line. */
void kvdb_dump(const kvdb_t *db);

#endif /* KVDB_H */
