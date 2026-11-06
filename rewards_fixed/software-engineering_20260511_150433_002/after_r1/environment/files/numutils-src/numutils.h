/*
 * numutils.h - Numerical Utilities Library
 * Version 1.2.3
 * 
 * A collection of numerical algorithms for scientific computing.
 * Last updated: 2005-03-15
 */

#ifndef NUMUTILS_H
#define NUMUTILS_H

#ifdef __cplusplus
extern "C" {
#endif

/* Configuration - adjust these for your platform */
#define NU_MAX_DIMENSIONS  100
#define NU_EPSILON         1e-12
#define NU_MAX_ITERATIONS  1000

/* Error codes */
#define NU_SUCCESS         0
#define NU_ERROR          -1
#define NU_SINGULAR       -2
#define NU_DIVERGE        -3

/* Array operations */
double nu_mean();
double nu_variance();
double nu_stddev();
int nu_sort();
int nu_sort_desc();
double nu_median();
double *nu_percentile();

/* Matrix operations */
double **nu_matrix_create();
void nu_matrix_free();
double nu_matrix_determinant();
int nu_matrix_lu_decompose();
double **nu_matrix_multiply();

/* Interpolation */
double nu_interp_linear();
double nu_interp_cubic();

/* Root finding */
double nu_root_bisection();
double nu_root_newton();

/* Integration */
double nu_integrate_trapezoid();
double nu_integrate_simpson();

/* Random number generation */
void nu_srand();
double nu_rand_uniform();
double nu_rand_normal();

/* Statistics */
double nu_correlation();
double nu_covariance();

/* Version */
const char *nu_version();

#ifdef __cplusplus
}
#endif

#endif /* NUMUTILS_H */
