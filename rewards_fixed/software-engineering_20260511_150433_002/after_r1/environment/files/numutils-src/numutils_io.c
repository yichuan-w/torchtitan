/*
 * numutils_io.c - I/O utilities for numutils library
 * 
 * File I/O and data loading functions.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "numutils.h"

/* 
 * Data file format utilities
 * These functions read/write arrays to files
 */

/*
 * Load an array of doubles from a file.
 * Format: first line is count, followed by one value per line
 */
double *
nu_load_array(filename, n)
    char *filename;
    int *n;
{
    FILE *fp;
    double *arr;
    int count, i;
    char line[256];
    
    fp = fopen(filename, "r");
    if (!fp) {
        return NULL;
    }
    
    /* Read count */
    if (!fgets(line, sizeof(line), fp)) {
        fclose(fp);
        return NULL;
    }
    
    count = atoi(line);
    if (count <= 0) {
        fclose(fp);
        return NULL;
    }
    
    arr = (double *)malloc(count * sizeof(double));
    if (!arr) {
        fclose(fp);
        return NULL;
    }
    
    for (i = 0; i < count; i++) {
        if (!fgets(line, sizeof(line), fp)) {
            free(arr);
            fclose(fp);
            return NULL;
        }
        arr[i] = atof(line);
    }
    
    *n = count;
    fclose(fp);
    return arr;
}

/*
 * Save an array of doubles to a file.
 */
int
nu_save_array(filename, arr, n)
    char *filename;
    double *arr;
    int n;
{
    FILE *fp;
    int i;
    
    fp = fopen(filename, "w");
    if (!fp) {
        return -1;
    }
    
    fprintf(fp, "%d\n", n);
    
    for (i = 0; i < n; i++) {
        fprintf(fp, "%.15g\n", arr[i]);
    }
    
    fclose(fp);
    return 0;
}

/*
 * Load a matrix from a file.
 * Format: first line is rows and cols, then row-major data
 */
double **
nu_load_matrix(filename, rows, cols)
    char *filename;
    int *rows, *cols;
{
    FILE *fp;
    double **mat;
    int r, c, i, j;
    char line[512];
    
    fp = fopen(filename, "r");
    if (!fp) {
        return NULL;
    }
    
    /* Read dimensions */
    if (!fgets(line, sizeof(line), fp)) {
        fclose(fp);
        return NULL;
    }
    
    if (sscanf(line, "%d %d", &r, &c) != 2) {
        fclose(fp);
        return NULL;
    }
    
    if (r <= 0 || c <= 0) {
        fclose(fp);
        return NULL;
    }
    
    mat = nu_matrix_create(r, c);
    if (!mat) {
        fclose(fp);
        return NULL;
    }
    
    for (i = 0; i < r; i++) {
        for (j = 0; j < c; j++) {
            if (!fgets(line, sizeof(line), fp)) {
                nu_matrix_free(mat, r);
                fclose(fp);
                return NULL;
            }
            mat[i][j] = atof(line);
        }
    }
    
    *rows = r;
    *cols = c;
    fclose(fp);
    return mat;
}

/*
 * Save a matrix to a file.
 */
int
nu_save_matrix(filename, mat, rows, cols)
    char *filename;
    double **mat;
    int rows, cols;
{
    FILE *fp;
    int i, j;
    
    fp = fopen(filename, "w");
    if (!fp) {
        return -1;
    }
    
    fprintf(fp, "%d %d\n", rows, cols);
    
    for (i = 0; i < rows; i++) {
        for (j = 0; j < cols; j++) {
            fprintf(fp, "%.15g\n", mat[i][j]);
        }
    }
    
    fclose(fp);
    return 0;
}

/*
 * Print array to stdout
 */
void
nu_print_array(arr, n)
    double *arr;
    int n;
{
    int i;
    printf("[");
    for (i = 0; i < n; i++) {
        printf("%.6g", arr[i]);
        if (i < n - 1) printf(", ");
    }
    printf("]\n");
}

/*
 * Print matrix to stdout
 */
void
nu_print_matrix(mat, rows, cols)
    double **mat;
    int rows, cols;
{
    int i, j;
    for (i = 0; i < rows; i++) {
        for (j = 0; j < cols; j++) {
            printf("%12.6g ", mat[i][j]);
        }
        printf("\n");
    }
}
