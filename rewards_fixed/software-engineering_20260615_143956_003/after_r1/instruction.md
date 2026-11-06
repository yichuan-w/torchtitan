The C extension module in `/app/ringbuffer.c` implements a fixed-size ring buffer for inter-thread communication. Build it with the provided `setup.py` and make it importable from Python.

The ring buffer is used in a long-running service that pushes thousands of objects per second. The ops team has noticed the process memory growing unboundedly over time. They've narrowed the leak down to this ring buffer module.

Find the memory leak in the C code, fix it, rebuild the extension, and verify your fix works.