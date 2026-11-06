A monitoring system crashed and the only surviving data is a binary log file at
`/app/sensor_data.bin` along with a broken C reader at `/app/logreader.c` and
a Makefile at `/app/Makefile`.

The reader was written hastily and never tested against real data. The
Makefile doesn't work and the C code has bugs — it crashes or produces
garbage when you try to compile and run it.

The binary file is in the BLOG format. It stores timestamped log entries
from sensor devices. The header contains a magic string, version, flags,
and an entry count. Each entry has a timestamp, log level, source name,
and message. The file ends with a footer containing a magic string and
a CRC32 checksum. All multi-byte integers are stored in little-endian
byte order.

The binary file itself has issues: the entry count recorded in the header
does not match the actual number of entries in the file, and the footer
checksum may not verify correctly.

Your task: fix the Makefile and `/app/logreader.c` so that running `make`
followed by `./logreader /app/sensor_data.bin` outputs valid JSON containing
**all log entries** that are actually present in the file, with correct
timestamp, level, source, and message fields for each entry.

The output must include every entry in the file, regardless of what the
header says. Each field in each entry must match the actual bytes stored
in the file.

You may modify the Makefile and `/app/logreader.c` in any way. Do not
modify the binary data file. Your solution is correct when the test suite
passes.