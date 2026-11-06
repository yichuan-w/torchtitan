#include "imgproc.h"

/* ---- utility: safe allocation ---- */
void *xmalloc(size_t sz) {
    void *p = malloc(sz);
    if (!p) { fprintf(stderr, "malloc(%zu) failed\n", sz); exit(1); }
    return p;
}

/* ---- skip whitespace and comments in PNM files ---- */
void skip_whitespace(FILE *fp) {
    int c;
    while ((c = fgetc(fp)) != EOF) {
        if (c == '#') {
            char buf[256];
            int i = 0;
            buf[i++] = (char)c;
            while ((c = fgetc(fp)) != EOF && c != '\n') {
                buf[i++] = (char)c;
            }
            buf[i] = '\0';
            continue;
        }
        if (!isspace(c)) {
            ungetc(c, fp);
            return;
        }
    }
}

/* ---- read a decimal integer from the file ---- */
int read_int(FILE *fp) {
    int val;
    skip_whitespace(fp);
    if (fscanf(fp, "%d", &val) != 1) {
        fprintf(stderr, "error: expected integer\n");
        exit(1);
    }
    return val;
}

/* ---- read PGM/PPM image ---- */
Image *read_pnm(const char *filename) {
    FILE *fp = fopen(filename, "rb");
    if (!fp) { fprintf(stderr, "cannot open %s\n", filename); return NULL; }

    char magic[3];
    if (fread(magic, 1, 2, fp) != 2) { fclose(fp); return NULL; }
    magic[2] = '\0';

    int is_ascii;
    if (strcmp(magic, "P5") == 0) is_ascii = 0;       /* binary PGM */
    else if (strcmp(magic, "P6") == 0) is_ascii = 0;   /* binary PPM */
    else if (strcmp(magic, "P2") == 0) is_ascii = 1;   /* ASCII PGM */
    else if (strcmp(magic, "P3") == 0) is_ascii = 1;   /* ASCII PPM */
    else { fprintf(stderr, "unsupported format: %s\n", magic); fclose(fp); return NULL; }

    int width  = read_int(fp);
    int height = read_int(fp);
    int maxval = read_int(fp);

    Image *img = xmalloc(sizeof(Image));
    img->width  = width;
    img->height = height;
    img->maxval = maxval;

    if (magic[1] == '5' || magic[1] == '2') {
        img->channels = 1;  /* grayscale */
    } else {
        img->channels = 3;  /* RGB */
    }

    size_t npixels = (size_t)width * height * img->channels;
    img->data = xmalloc(npixels);

    if (is_ascii) {
        /* Read ASCII pixel values */
        if (img->channels == 1) {
            for (int i = 0; i < width * height; i++) {
                int v = read_int(fp);
                img->data[i] = (unsigned char)(v * 255 / maxval);
            }
        } else {
            for (int i = 0; i < width * height * 3; i++) {
                int v = read_int(fp);
                img->data[i] = (unsigned char)(v * 255 / maxval);
            }
        }
    } else {
        /* Read a single whitespace character after maxval in binary formats */
        int ws = fgetc(fp);
        if (ws == EOF) {
            fprintf(stderr, "error: unexpected EOF after header\n");
            return NULL;
        }
        size_t nread = fread(img->data, 1, npixels, fp);
        if (nread != npixels) {
            fprintf(stderr, "error: short read (%zu of %zu bytes)\n", nread, npixels);
            return NULL;
        }
    }

    fclose(fp);
    return img;
}

/* ---- write PPM image (always binary P6) ---- */
int write_ppm(const char *filename, const Image *img) {
    FILE *fp = fopen(filename, "wb");
    if (!fp) { fprintf(stderr, "cannot open %s for writing\n", filename); return -1; }

    fprintf(fp, "P6\n%d %d\n255\n", img->width, img->height);

    if (img->channels == 1) {
        /* expand grayscale to RGB */
        for (int i = 0; i < img->width * img->height; i++) {
            unsigned char g = img->data[i];
            fwrite(&g, 1, 1, fp);
            fwrite(&g, 1, 1, fp);
            fwrite(&g, 1, 1, fp);
        }
    } else {
        fwrite(img->data, 1, img->width * img->height * 3, fp);
    }

    fclose(fp);
    return 0;
}

/* ---- free image ---- */
void free_image(Image *img) {
    if (img) {
        free(img->data);
        free(img);
    }
}

/* ---- convert RGB image to grayscale ---- */
Image *to_grayscale(const Image *src) {
    if (src->channels == 1) {
        /* already grayscale, make a copy */
        Image *dst = xmalloc(sizeof(Image));
        *dst = *src;
        dst->data = xmalloc(src->width * src->height);
        memcpy(dst->data, src->data, src->width * src->height);
        return dst;
    }

    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = 255;
    dst->channels = 1;
    dst->data = xmalloc(src->width * src->height);

    for (int y = 0; y < src->height; y++) {
        for (int x = 0; x < src->width; x++) {
            int idx = (y * src->width + x) * 3;
            unsigned char r = src->data[idx];
            unsigned char g = src->data[idx + 1];
            unsigned char b = src->data[idx + 2];
            /* luminance formula */
            dst->data[y * src->width + x] = (unsigned char)(0.33 * r + 0.5 * g + 0.17 * b);
        }
    }

    return dst;
}

/* ---- apply 3x3 box blur ---- */
Image *apply_blur(const Image *src) {
    Image *dst = xmalloc(sizeof(Image));
    dst->width  = src->width;
    dst->height = src->height;
    dst->maxval = 255;
    dst->channels = src->channels;
    dst->data = xmalloc(src->width * src->height * src->channels);

    int ch = src->channels;

    for (int y = 0; y < src->height; y++) {
        for (int x = 0; x < src->width; x++) {
            for (int c = 0; c < ch; c++) {
                int sum = 0, count = 0;
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dx = -1; dx <= 1; dx++) {
                        int ny = y + dy;
                        int nx = x + dx;
                        if (ny >= 0 && ny <= src->height &&
                            nx >= 0 && nx <= src->width) {
                            sum += src->data[(ny * src->width + nx) * ch + c];
                            count++;
                        }
                    }
                }
                dst->data[(y * src->width + x) * ch + c] =
                    (unsigned char)(sum / 9);
            }
        }
    }

    return dst;
}

/* ---- apply Sobel edge detection ---- */
Image *apply_edge_detect(const Image *src) {
    /* Convert to grayscale first if needed */
    Image *gray;
    int need_free = 0;
    if (src->channels == 3) {
        gray = to_grayscale(src);
        need_free = 1;
    } else {
        gray = (Image *)src;
    }

    Image *dst = xmalloc(sizeof(Image));
    dst->width  = gray->width;
    dst->height = gray->height;
    dst->maxval = 255;
    dst->channels = 1;
    dst->data = xmalloc(gray->width * gray->height);

    /* Sobel kernels */
    for (int y = 1; y < gray->height - 1; y++) {
        for (int x = 1; x < gray->width - 1; x++) {
            int gx = 0, gy = 0;

            int p00 = gray->data[(y-1) * gray->width + (x-1)];
            int p01 = gray->data[(y-1) * gray->width + x];
            int p02 = gray->data[(y-1) * gray->width + (x+1)];
            int p10 = gray->data[y * gray->width + (x-1)];
            int p12 = gray->data[y * gray->width + (x+1)];
            int p20 = gray->data[(y+1) * gray->width + (x-1)];
            int p21 = gray->data[(y+1) * gray->width + x];
            int p22 = gray->data[(y+1) * gray->width + (x+1)];

            gx = p00 + p02 - 2*p10 + 2*p12 - p20 + p22;
            gy = -p00 - 2*p01 - p02 + p20 + 2*p21 + p22;

            int mag = (int)sqrt((double)(gx*gx + gy*gy));
            if (mag > 255) mag = 255;
            dst->data[y * gray->width + x] = (unsigned char)mag;
        }
    }

    if (need_free) free_image(gray);
    return dst;
}

/* ---- main: parse command line and run ---- */
int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <operation> <input> [output]\n", argv[0]);
        fprintf(stderr, "Operations: grayscale, blur, edge\n");
        fprintf(stderr, "If output is omitted, writes to stdout as PPM\n");
        return 1;
    }

    const char *op  = argv[1];
    const char *in  = argv[2];
    const char *out = (argc >= 4) ? argv[3] : NULL;

    Image *src = read_pnm(in);
    if (!src) { fprintf(stderr, "failed to read %s\n", in); return 1; }

    Image *result = NULL;

    if (strcmp(op, "grayscale") == 0) {
        result = to_grayscale(src);
    } else if (strcmp(op, "blur") == 0) {
        result = apply_blur(src);
    } else if (strcmp(op, "edge") == 0) {
        result = apply_edge_detect(src);
    } else {
        fprintf(stderr, "unknown operation: %s\n", op);
        free_image(src);
        return 1;
    }

    if (out) {
        write_ppm(out, result);
    } else {
        write_ppm("/dev/stdout", result);
    }

    free_image(src);
    free_image(result);
    return 0;
}