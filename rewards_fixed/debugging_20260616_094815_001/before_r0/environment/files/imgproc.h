/* imgproc.h - Main image processing header */

#ifndef IMGPROC_H
#define IMGPROC_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>

typedef struct {
    int width, height, maxval;
    int channels;            /* 1 = grayscale, 3 = RGB */
    unsigned char *data;     /* row-major pixel data */
} Image;

/* Memory allocation that exits on failure */
void *xmalloc(size_t sz);

/* Read/write images */
Image *read_pnm(const char *filename);
int write_ppm(const char *filename, const Image *img);
void free_image(Image *img);

/* Core operations */
Image *to_grayscale(const Image *src);
Image *apply_blur(const Image *src);
Image *apply_edge_detect(const Image *src);

/* Utility: skip whitespace and comments in PNM files */
void skip_whitespace(FILE *fp);

/* Utility: read an integer from PNM header */
int read_int(FILE *fp);

#endif /* IMGPROC_H */