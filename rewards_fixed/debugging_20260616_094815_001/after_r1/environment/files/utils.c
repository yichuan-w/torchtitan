/* utils.c - Additional image processing utilities */

#include "utils.h"

/* ---- flip image vertically ---- */
Image *flip_vertical(const Image *src) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = src->maxval;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int row_size = src->width * src->channels;
    for (int y = 0; y < src->height; y++) {
        int src_row = y * row_size;
        int dst_row = (src->height - 1 - y) * row_size;
        memcpy(dst->data + dst_row, src->data + src_row, row_size);
    }

    return dst;
}

/* ---- flip image horizontally ---- */
Image *flip_horizontal(const Image *src) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = src->maxval;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int ch = src->channels;
    for (int y = 0; y < src->height; y++) {
        for (int x = 0; x < src->width; x++) {
            int src_idx = (y * src->width + x) * ch;
            int dst_idx = (y * src->width + (src->width - 1 - x)) * ch;
            for (int c = 0; c < ch; c++) {
                dst->data[dst_idx + c] = src->data[src_idx + c];
            }
        }
    }

    return dst;
}

/* ---- rotate 90 degrees clockwise ---- */
Image *rotate_90(const Image *src) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->height;
    dst->height = src->width;
    dst->maxval = src->maxval;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int ch = src->channels;
    for (int y = 0; y < src->height; y++) {
        for (int x = 0; x < src->width; x++) {
            int src_idx = (y * src->width + x) * ch;
            int dst_idx = (x * dst->width + (dst->width - 1 - y)) * ch;
            for (int c = 0; c < ch; c++) {
                dst->data[dst_idx + c] = src->data[src_idx + c];
            }
        }
    }

    return dst;
}

/* ---- adjust brightness ---- */
Image *adjust_brightness(const Image *src, int delta) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = src->maxval;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int npixels = src->width * src->height * src->channels;
    for (int i = 0; i < npixels; i++) {
        int v = (int)src->data[i] + delta;
        if (v < 0) v = 0;
        if (v > 255) v = 255;
        dst->data[i] = (unsigned char)v;
    }

    return dst;
}

/* ---- invert colors ---- */
Image *invert(const Image *src) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = src->maxval;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int npixels = src->width * src->height * src->channels;
    for (int i = 0; i < npixels; i++) {
        dst->data[i] = (unsigned char)(255 - src->data[i]);
    }

    return dst;
}

/* ---- calculate histogram for a specific channel ---- */
int *histogram(const Image *src, int channel) {
    if (channel < 0 || channel >= src->channels) {
        fprintf(stderr, "invalid channel %d (max %d)\n", channel, src->channels - 1);
        return NULL;
    }

    int *hist = calloc(256, sizeof(int));
    if (!hist) return NULL;

    for (int i = 0; i < src->width * src->height; i++) {
        int val = src->data[i * src->channels + channel];
        hist[val]++;
    }

    return hist;
}