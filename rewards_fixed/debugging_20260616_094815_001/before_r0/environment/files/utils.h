/* utils.h - Additional image processing utilities */

#ifndef UTILS_H
#define UTILS_H

#include "imgproc.h"

/* Flip image vertically */
Image *flip_vertical(const Image *src);

/* Flip image horizontally */
Image *flip_horizontal(const Image *src);

/* Rotate 90 degrees clockwise */
Image *rotate_90(const Image *src);

/* Adjust brightness by adding delta to each channel */
Image *adjust_brightness(const Image *src, int delta);

/* Invert colors */
Image *invert(const Image *src);

/* Calculate image histogram (returns array of 256 ints per channel) */
int *histogram(const Image *src, int channel);

#endif /* UTILS_H */