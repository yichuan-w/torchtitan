from setuptools import setup, Extension

setup(
    name='ringbuffer',
    version='1.0.0',
    description='A fixed-size ring buffer implemented as a C extension.',
    ext_modules=[Extension('ringbuffer', ['ringbuffer.c'])],
)