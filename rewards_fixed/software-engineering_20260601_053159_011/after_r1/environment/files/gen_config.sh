#!/bin/bash
# Generate kvdb_config.h with build configuration
cat > kvdb_config.h << 'EOF'
#ifndef KVDB_CONFIG_H
#define KVDB_CONFIG_H

#define KVDB_VERSION "1.0.0"
#define KVDB_MAX_TABLE_SIZE 1024
#define KVDB_DEFAULT_PORT 9876

#endif /* KVDB_CONFIG_H */
EOF
