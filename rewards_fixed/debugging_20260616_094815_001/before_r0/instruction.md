I've been working on a C image processing utility called `imgproc` that handles PGM/PPM images. It should support three operations: grayscale conversion, box blur, and Sobel edge detection.

The source is in `/app/imgproc.c` with a Makefile at `/app/Makefile`. Build with `make`. Usage: `./imgproc <operation> <input.ppm> [output.ppm]`

It compiles fine but the output is wrong for some operations and it crashes on certain inputs. I need you to track down and fix whatever issues you find. There are some test images in `/app/` (small.ppm, test_img.ppm, sample3.ppm).

Run `valgrind --leak-check=full ./imgproc ...` to check for memory issues.