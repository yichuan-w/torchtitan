ARCV1 ARCHIVE FORMAT
====================

An ARCV1 archive consists of three sequential sections:

[1] HEADER (16 bytes)
    Bytes  0-3:  Magic 'ARC' followed by version byte 0x01
    Bytes  4-7:  File count (uint32 LE)
    Bytes  8-11: Index offset (uint32 LE) - byte position where index begins
    Bytes 12-15: Reserved (must be zero)

[2] INDEX (file_count * 40 bytes)
    Each 40-byte entry:
      Bytes  0-31: Filename (UTF-8, null-padded)
      Bytes 32-35: Data offset (uint32 LE) - byte position of file data
      Bytes 36-39: Data length (uint32 LE) - uncompressed size in bytes
    Entries appear in alphabetical order by filename.

[3] DATA
    Raw file contents concatenated in the same order as index entries.
    No compression or padding is applied.
    Each file's data begins at its data_offset for data_length bytes.

The index always begins immediately after the header (at byte offset 16).
Data starts at byte offset (16 + file_count * 40).