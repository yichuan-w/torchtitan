/*
 * binparse_test.c - Test program for binparse library
 */

#include "binparse.h"
#include <stdio.h>
#include <stdlib.h>

static const char *error_str(int err) {
    switch (err) {
        case PARSE_OK:      return "OK";
        case PARSE_ERR_IO:  return "IO error";
        case PARSE_ERR_MAGIC: return "Invalid magic/version";
        case PARSE_ERR_CSUM: return "Checksum mismatch";
        case PARSE_ERR_SIZE: return "Invalid size";
        default:            return "Unknown error";
    }
}

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file1.bin> [file2.bin ...]\n", argv[0]);
        return 1;
    }
    
    int all_ok = 1;
    
    for (int i = 1; i < argc; i++) {
        printf("Parsing %s... ", argv[i]);
        fflush(stdout);
        
        int result = binparse_file(argv[i]);
        
        if (result == PARSE_OK) {
            printf("OK\n");
        } else {
            printf("FAILED: %s\n", error_str(result));
            all_ok = 0;
        }
    }
    
    if (all_ok) {
        printf("\nAll files parsed successfully.\n");
        return 0;
    } else {
        printf("\nSome files failed to parse.\n");
        return 1;
    }
}
