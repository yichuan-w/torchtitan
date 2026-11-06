/*
 * test_numutils.c - Test program for numutils library
 * 
 * This program tests the functionality of the numutils library.
 * DO NOT MODIFY THIS FILE.
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include "numutils.h"

#define TOLERANCE 1e-6

static int tests_passed = 0;
static int tests_failed = 0;

static void
test_assert(condition, test_name)
    int condition;
    char *test_name;
{
    if (condition) {
        printf("PASS: %s\n", test_name);
        tests_passed++;
    } else {
        printf("FAIL: %s\n", test_name);
        tests_failed++;
    }
}

static double
test_func_square(x)
    double x;
{
    return x * x - 4.0; /* Root at x = 2 */
}

static double
test_func_square_deriv(x)
    double x;
{
    return 2.0 * x;
}

static double
test_func_sin(x)
    double x;
{
    return sin(x);
}

int
main(argc, argv)
    int argc;
    char **argv;
{
    double arr[] = {3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0};
    double arr2[] = {1.0, 2.0, 3.0, 4.0, 5.0};
    double x_vals[] = {0.0, 1.0, 2.0, 3.0, 4.0};
    double y_vals[] = {0.0, 1.0, 4.0, 9.0, 16.0};
    double percentiles[] = {25.0, 50.0, 75.0};
    double *result;
    double **mat, **mat2, **mat_result;
    double det, root, integral;
    int perm[3];
    
    printf("=== numutils Library Tests ===\n\n");
    
    /* Version test */
    printf("-- Version --\n");
    test_assert(strcmp(nu_version(), "1.2.3") == 0, "Version string");
    
    /* Array operations */
    printf("\n-- Array Operations --\n");
    test_assert(fabs(nu_mean(arr2, 5) - 3.0) < TOLERANCE, "Mean calculation");
    test_assert(nu_variance(arr2, 5) > 0, "Variance calculation");
    test_assert(nu_stddev(arr2, 5) > 0, "Standard deviation calculation");
    
    result = nu_percentile(arr2, 5, percentiles, 3);
    test_assert(result != NULL, "Percentile allocation");
    if (result) {
        test_assert(fabs(result[1] - 3.0) < TOLERANCE, "Median via percentile");
        free(result);
    }
    
    /* Sorting */
    printf("\n-- Sorting --\n");
    double sort_arr[] = {5.0, 2.0, 8.0, 1.0, 9.0};
    nu_sort(sort_arr, 5);
    test_assert(sort_arr[0] == 1.0 && sort_arr[4] == 9.0, "Sort ascending");
    
    nu_sort_desc(sort_arr, 5);
    test_assert(sort_arr[0] == 9.0 && sort_arr[4] == 1.0, "Sort descending");
    
    /* Median */
    printf("\n-- Statistics --\n");
    double med_arr[] = {1.0, 3.0, 2.0, 4.0, 5.0};
    test_assert(fabs(nu_median(med_arr, 5) - 3.0) < TOLERANCE, "Median (odd)");
    
    double med_arr2[] = {1.0, 2.0, 3.0, 4.0};
    test_assert(fabs(nu_median(med_arr2, 4) - 2.5) < TOLERANCE, "Median (even)");
    
    /* Matrix operations */
    printf("\n-- Matrix Operations --\n");
    mat = nu_matrix_create(3, 3);
    test_assert(mat != NULL, "Matrix creation");
    
    if (mat) {
        /* Identity matrix */
        mat[0][0] = 1.0; mat[0][1] = 0.0; mat[0][2] = 0.0;
        mat[1][0] = 0.0; mat[1][1] = 1.0; mat[1][2] = 0.0;
        mat[2][0] = 0.0; mat[2][1] = 0.0; mat[2][2] = 1.0;
        
        det = nu_matrix_determinant(mat, 3);
        test_assert(fabs(det - 1.0) < TOLERANCE, "Determinant of identity matrix");
        
        nu_matrix_free(mat, 3);
    }
    
    /* Matrix multiplication */
    mat = nu_matrix_create(2, 2);
    mat2 = nu_matrix_create(2, 2);
    
    if (mat && mat2) {
        mat[0][0] = 1.0; mat[0][1] = 2.0;
        mat[1][0] = 3.0; mat[1][1] = 4.0;
        
        mat2[0][0] = 5.0; mat2[0][1] = 6.0;
        mat2[1][0] = 7.0; mat2[1][1] = 8.0;
        
        mat_result = nu_matrix_multiply(mat, mat2, 2, 2, 2);
        test_assert(mat_result != NULL, "Matrix multiplication allocation");
        
        if (mat_result) {
            /* Expected: [[19, 22], [43, 50]] */
            test_assert(fabs(mat_result[0][0] - 19.0) < TOLERANCE, "Matrix multiply [0][0]");
            test_assert(fabs(mat_result[0][1] - 22.0) < TOLERANCE, "Matrix multiply [0][1]");
            test_assert(fabs(mat_result[1][0] - 43.0) < TOLERANCE, "Matrix multiply [1][0]");
            test_assert(fabs(mat_result[1][1] - 50.0) < TOLERANCE, "Matrix multiply [1][1]");
            nu_matrix_free(mat_result, 2);
        }
        
        nu_matrix_free(mat, 2);
        nu_matrix_free(mat2, 2);
    }
    
    /* Interpolation */
    printf("\n-- Interpolation --\n");
    test_assert(fabs(nu_interp_linear(x_vals, y_vals, 5, 1.5) - 2.5) < TOLERANCE, 
                "Linear interpolation");
    
    double cubic_result = nu_interp_cubic(x_vals, y_vals, 5, 1.5);
    test_assert(cubic_result >= 0, "Cubic interpolation returns value");
    
    /* Root finding */
    printf("\n-- Root Finding --\n");
    root = nu_root_bisection(test_func_square, 0.0, 4.0, 1e-6, 100);
    test_assert(fabs(root - 2.0) < 1e-4, "Bisection root finding");
    
    root = nu_root_newton(test_func_square, test_func_square_deriv, 1.0, 1e-6, 100);
    test_assert(fabs(root - 2.0) < 1e-4, "Newton root finding");
    
    /* Integration */
    printf("\n-- Integration --\n");
    integral = nu_integrate_trapezoid(test_func_sin, 0.0, 3.14159, 1000);
    test_assert(fabs(integral - 2.0) < 0.01, "Trapezoid integration");
    
    integral = nu_integrate_simpson(test_func_sin, 0.0, 3.14159, 1000);
    test_assert(fabs(integral - 2.0) < 0.001, "Simpson integration");
    
    /* Random numbers */
    printf("\n-- Random Numbers --\n");
    nu_srand(42);
    double r1 = nu_rand_uniform(0.0, 1.0);
    double r2 = nu_rand_uniform(0.0, 1.0);
    test_assert(r1 >= 0.0 && r1 <= 1.0, "Uniform random range");
    test_assert(r1 != r2, "Random values differ");
    
    nu_srand(42);
    double r3 = nu_rand_uniform(0.0, 1.0);
    test_assert(fabs(r1 - r3) < TOLERANCE, "Random seed reproducibility");
    
    /* Statistics */
    printf("\n-- Correlation/Covariance --\n");
    double x_corr[] = {1.0, 2.0, 3.0, 4.0, 5.0};
    double y_corr[] = {2.0, 4.0, 6.0, 8.0, 10.0};
    
    double corr = nu_correlation(x_corr, y_corr, 5);
    test_assert(fabs(corr - 1.0) < TOLERANCE, "Perfect positive correlation");
    
    double cov = nu_covariance(x_corr, y_corr, 5);
    test_assert(cov > 0, "Positive covariance");
    
    /* Summary */
    printf("\n=== Test Summary ===\n");
    printf("Passed: %d\n", tests_passed);
    printf("Failed: %d\n", tests_failed);
    
    return tests_failed > 0 ? 1 : 0;
}
