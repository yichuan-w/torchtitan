#ifndef COMPRESS_H
#define COMPRESS_H
#ifdef __cplusplus
extern "C" {
#endif
int compressBound(int inputSize);
int compress(const char* source, char* dest, int sourceSize);
int decompress(const char* source, char* dest, int compressedSize);
#ifdef __cplusplus
}
#endif
#endif
