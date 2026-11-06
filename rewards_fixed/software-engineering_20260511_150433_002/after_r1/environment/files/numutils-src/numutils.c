/*
 * numutils.c - Numerical Utilities Library Implementation
 * 
 * Main library implementation file.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "numutils.h"

/* Legacy code note: math functions were declared implicitly in older compilers */

/* Internal state for random number generator */
static unsigned long nu_rand_state = 1;

/* Internal sorting helper - uses qsort */
static int
compare_doubles(a, b)
    double *a;
    double *b;
{
    if (*a < *b) return -1;
    if (*a > *b) return 1;
    return 0;
}

static int
compare_doubles_desc(a, b)
    double *a;
    double *b;
{
    if (*a > *b) return -1;
    if (*a < *b) return 1;
    return 0;
}

/* Version string */
const char *
nu_version()
{
    return "1.2.3";
}

/*
 * Array Operations
 */

double
nu_mean(arr, n)
    double *arr;
    int n;
{
    double sum = 0.0;
    int i;
    
    if (n <= 0) return 0.0;
    
    for (i = 0; i < n; i++) {
        sum += arr[i];
    }
    
    return sum / n;
}

double
nu_variance(arr, n)
    double *arr;
    int n;
{
    double mean, sum_sq, diff;
    int i;
    
    if (n <= 1) return 0.0;
    
    mean = nu_mean(arr, n);
    sum_sq = 0.0;
    
    for (i = 0; i < n; i++) {
        diff = arr[i] - mean;
        sum_sq += diff * diff;
    }
    
    return sum_sq / (n - 1);
}

double
nu_stddev(arr, n)
    double *arr;
    int n;
{
    return sqrt(nu_variance(arr, n));
}

int
nu_sort(arr, n)
    double *arr;
    int n;
{
    if (n <= 0) return -1;
    
    qsort(arr, n, sizeof(double), compare_doubles);
    return 0;
}

int
nu_sort_desc(arr, n)
    double *arr;
    int n;
{
    if (n <= 0) return -1;
    
    qsort(arr, n, sizeof(double), compare_doubles_desc);
    return 0;
}

double
nu_median(arr, n)
    double *arr;
    int n;
{
    double *copy;
    double result;
    
    if (n <= 0) return 0.0;
    
    copy = (double *)malloc(n * sizeof(double));
    if (!copy) return 0.0;
    
    memcpy(copy, arr, n * sizeof(double));
    nu_sort(copy, n);
    
    if (n % 2 == 0) {
        result = (copy[n/2 - 1] + copy[n/2]) / 2.0;
    } else {
        result = copy[n/2];
    }
    
    free(copy);
    return result;
}

double *
nu_percentile(arr, n, percentiles, np)
    double *arr;
    int n;
    double *percentiles;
    int np;
{
    double *sorted, *result;
    int i, idx;
    
    if (n <= 0 || np <= 0) return NULL;
    
    sorted = (double *)malloc(n * sizeof(double));
    result = (double *)malloc(np * sizeof(double));
    
    if (!sorted || !result) {
        free(sorted);
        free(result);
        return NULL;
    }
    
    memcpy(sorted, arr, n * sizeof(double));
    nu_sort(sorted, n);
    
    for (i = 0; i < np; i++) {
        idx = (int)(percentiles[i] / 100.0 * (n - 1));
        if (idx < 0) idx = 0;
        if (idx >= n) idx = n - 1;
        result[i] = sorted[idx];
    }
    
    free(sorted);
    return result;
}

/*
 * Matrix Operations
 */

double **
nu_matrix_create(rows, cols)
    int rows, cols;
{
    double **mat;
    int i;
    
    mat = (double **)malloc(rows * sizeof(double *));
    if (!mat) return NULL;
    
    for (i = 0; i < rows; i++) {
        mat[i] = (double *)calloc(cols, sizeof(double));
        if (!mat[i]) {
            while (--i >= 0) free(mat[i]);
            free(mat);
            return NULL;
        }
    }
    
    return mat;
}

void
nu_matrix_free(mat, rows)
    double **mat;
    int rows;
{
    int i;
    if (!mat) return;
    
    for (i = 0; i < rows; i++) {
        free(mat[i]);
    }
    free(mat);
}

double
nu_matrix_determinant(mat, n)
    double **mat;
    int n;
{
    double det = 1.0;
    double **temp;
    int *perm;
    int i;
    
    if (n <= 0) return 0.0;
    if (n == 1) return mat[0][0];
    
    temp = nu_matrix_create(n, n);
    perm = (int *)malloc(n * sizeof(int));
    
    if (!temp || !perm) {
        nu_matrix_free(temp, n);
        free(perm);
        return 0.0;
    }
    
    /* Copy matrix */
    for (i = 0; i < n; i++) {
        memcpy(temp[i], mat[i], n * sizeof(double));
    }
    
    /* LU decomposition */
    if (nu_matrix_lu_decompose(temp, n, perm) != 0) {
        nu_matrix_free(temp, n);
        free(perm);
        return 0.0;
    }
    
    /* Product of diagonal elements */
    for (i = 0; i < n; i++) {
        det *= temp[i][i];
    }
    
    /* Adjust for row swaps */
    for (i = 0; i < n; i++) {
        if (perm[i] != i) det = -det;
    }
    
    nu_matrix_free(temp, n);
    free(perm);
    
    return det;
}

int
nu_matrix_lu_decompose(mat, n, perm)
    double **mat;
    int n;
    int *perm;
{
    int i, j, k, maxrow;
    double maxval, scale;
    double *rowtmp;
    
    if (n <= 0 || !mat || !perm) return -1;
    
    /* Initialize permutation */
    for (i = 0; i < n; i++) perm[i] = i;
    
    for (k = 0; k < n - 1; k++) {
        /* Find pivot */
        maxval = fabs(mat[k][k]);
        maxrow = k;
        
        for (i = k + 1; i < n; i++) {
            if (fabs(mat[i][k]) > maxval) {
                maxval = fabs(mat[i][k]);
                maxrow = i;
            }
        }
        
        /* Swap rows */
        if (maxrow != k) {
            rowtmp = mat[k]; 
            mat[k] = mat[maxrow]; 
            mat[maxrow] = rowtmp;
            
            j = perm[k]; 
            perm[k] = perm[maxrow]; 
            perm[maxrow] = j;
        }
        
        /* Check for singular matrix */
        if (fabs(mat[k][k]) < 1e-12) return -1;
        
        /* Eliminate column */
        for (i = k + 1; i < n; i++) {
            scale = mat[i][k] / mat[k][k];
            mat[i][k] = scale;
            for (j = k + 1; j < n; j++) {
                mat[i][j] -= scale * mat[k][j];
            }
        }
    }
    
    return 0;
}

double **
nu_matrix_multiply(a, b, m, n, p)
    double **a, **b;
    int m, n, p;
{
    double **result;
    int i, j, k;
    
    result = nu_matrix_create(m, p);
    if (!result) return NULL;
    
    for (i = 0; i < m; i++) {
        for (j = 0; j < p; j++) {
            result[i][j] = 0.0;
            for (k = 0; k < n; k++) {
                result[i][j] += a[i][k] * b[k][j];
            }
        }
    }
    
    return result;
}

/*
 * Interpolation
 */

double
nu_interp_linear(x, y, n, xval)
    double *x, *y;
    int n;
    double xval;
{
    int i;
    double t;
    
    if (n < 2) return 0.0;
    
    /* Find interval */
    for (i = 0; i < n - 1; i++) {
        if (xval >= x[i] && xval <= x[i+1]) {
            t = (xval - x[i]) / (x[i+1] - x[i]);
            return y[i] + t * (y[i+1] - y[i]);
        }
    }
    
    /* Extrapolate */
    if (xval < x[0]) {
        t = (xval - x[0]) / (x[1] - x[0]);
        return y[0] + t * (y[1] - y[0]);
    } else {
        t = (xval - x[n-2]) / (x[n-1] - x[n-2]);
        return y[n-2] + t * (y[n-1] - y[n-2]);
    }
}

double
nu_interp_cubic(x, y, n, xval)
    double *x, *y;
    int n;
    double xval;
{
    /* Simplified cubic interpolation - falls back to linear for edge cases */
    int i, idx;
    double dx, a, b, c, d, t;
    
    if (n < 2) return 0.0;
    if (n < 4) return nu_interp_linear(x, y, n, xval);
    
    /* Find interval */
    idx = -1;
    for (i = 0; i < n - 1; i++) {
        if (xval >= x[i] && xval <= x[i+1]) {
            idx = i;
            break;
        }
    }
    
    if (idx < 0) {
        return nu_interp_linear(x, y, n, xval);
    }
    
    /* Adjust for boundary */
    if (idx == 0) idx = 1;
    if (idx == n - 2) idx = n - 3;
    
    i = idx;
    dx = x[i+1] - x[i];
    t = (xval - x[i]) / dx;
    
    /* Simple cubic interpolation coefficients */
    a = y[i];
    b = (y[i+1] - y[i-1]) / 2.0;
    c = (3.0 * (y[i+1] - y[i]) - (y[i+2] - y[i])) / 2.0;
    d = (y[i+2] - y[i]) - 2.0 * (y[i+1] - y[i]);
    
    return a + b * t + c * t * t + d * t * t * t;
}

/*
 * Root Finding
 */

double
nu_root_bisection(f, a, b, tol, maxiter)
    double (*f)();
    double a, b, tol;
    int maxiter;
{
    double fa, fb, fm, mid;
    int iter;
    
    fa = f(a);
    fb = f(b);
    
    if (fa * fb > 0) return a; /* No root in interval */
    
    for (iter = 0; iter < maxiter; iter++) {
        mid = (a + b) / 2.0;
        fm = f(mid);
        
        if (fabs(fm) < tol || fabs(b - a) < tol) {
            return mid;
        }
        
        if (fa * fm < 0) {
            b = mid;
            fb = fm;
        } else {
            a = mid;
            fa = fm;
        }
    }
    
    return (a + b) / 2.0;
}

double
nu_root_newton(f, df, x0, tol, maxiter)
    double (*f)(), (*df)();
    double x0, tol;
    int maxiter;
{
    double x, fx, dfx;
    int iter;
    
    x = x0;
    
    for (iter = 0; iter < maxiter; iter++) {
        fx = f(x);
        dfx = df(x);
        
        if (fabs(dfx) < 1e-15) break;
        
        x = x - fx / dfx;
        
        if (fabs(f(x)) < tol) return x;
    }
    
    return x;
}

/*
 * Integration
 */

double
nu_integrate_trapezoid(f, a, b, n)
    double (*f)();
    double a, b;
    int n;
{
    double h, sum;
    int i;
    
    if (n < 1) return 0.0;
    
    h = (b - a) / n;
    sum = 0.5 * (f(a) + f(b));
    
    for (i = 1; i < n; i++) {
        sum += f(a + i * h);
    }
    
    return sum * h;
}

double
nu_integrate_simpson(f, a, b, n)
    double (*f)();
    double a, b;
    int n;
{
    double h, sum;
    int i;
    
    if (n < 2 || n % 2 != 0) n = (n / 2) * 2 + 2;
    
    h = (b - a) / n;
    sum = f(a) + f(b);
    
    for (i = 1; i < n; i++) {
        if (i % 2 == 0) {
            sum += 2.0 * f(a + i * h);
        } else {
            sum += 4.0 * f(a + i * h);
        }
    }
    
    return sum * h / 3.0;
}

/*
 * Random Number Generation
 */

void
nu_srand(seed)
    unsigned int seed;
{
    nu_rand_state = seed ? seed : 1;
}

double
nu_rand_uniform(a, b)
    double a, b;
{
    /* Simple LCG random number generator */
    nu_rand_state = nu_rand_state * 1103515245 + 12345;
    return a + (b - a) * ((nu_rand_state >> 16) & 0x7fff) / 32768.0;
}

double
nu_rand_normal(mean, stddev)
    double mean, stddev;
{
    /* Box-Muller transform */
    double u1, u2, r;
    double two_pi;
    
    /* Legacy code: compute 2*pi manually to avoid M_PI portability issues */
    two_pi = 2.0 * M_PI;  /* M_PI should come from math.h */
    
    u1 = nu_rand_uniform(0.0, 1.0);
    u2 = nu_rand_uniform(0.0, 1.0);
    
    r = sqrt(-2.0 * log(u1 + 1e-10));
    
    return mean + stddev * r * cos(two_pi * u2);
}

/*
 * Statistics
 */

double
nu_correlation(x, y, n)
    double *x, *y;
    int n;
{
    double mean_x, mean_y, sum_xy, sum_xx, sum_yy;
    int i;
    
    if (n < 2) return 0.0;
    
    mean_x = nu_mean(x, n);
    mean_y = nu_mean(y, n);
    
    sum_xy = sum_xx = sum_yy = 0.0;
    
    for (i = 0; i < n; i++) {
        sum_xy += (x[i] - mean_x) * (y[i] - mean_y);
        sum_xx += (x[i] - mean_x) * (x[i] - mean_x);
        sum_yy += (y[i] - mean_y) * (y[i] - mean_y);
    }
    
    if (sum_xx < 1e-15 || sum_yy < 1e-15) return 0.0;
    
    return sum_xy / sqrt(sum_xx * sum_yy);
}

double
nu_covariance(x, y, n)
    double *x, *y;
    int n;
{
    double mean_x, mean_y, sum;
    int i;
    
    if (n < 2) return 0.0;
    
    mean_x = nu_mean(x, n);
    mean_y = nu_mean(y, n);
    
    sum = 0.0;
    for (i = 0; i < n; i++) {
        sum += (x[i] - mean_x) * (y[i] - mean_y);
    }
    
    return sum / (n - 1);
}
