I need a Node.js program `parse.js` that reads Apple binary property list (.plist) files and outputs their contents as JSON to stdout.

Usage: `node parse.js <input.plist>`

The binary plist format is Apple's binary serialization format. The file structure is:
- Header (8 bytes): magic "bplist" followed by 2-byte version (typically "00")
- Object table (variable): serialized objects
- Offset table (variable): array of file offsets pointing to each object
- Trailer (32 bytes, at end of file): contains offset_size, ref_size, num_objects, top_object, and offset_table_start

The trailer fields (all big-endian):
- Bytes 0-5: unused
- Byte 6: offset_size (1, 2, 4, or 8)
- Byte 7: ref_size (1, 2, 4, or 8)
- Bytes 8-15: num_objects (uint64)
- Bytes 16-23: top_object (uint64, index into offset table)
- Bytes 24-31: offset_table_start (uint64, file offset of offset table)

Each object is identified by a marker byte. The upper nibble is the type, the lower nibble is size info:
- 0x00: null
- 0x08: false
- 0x09: true
- 0x1X: integer (size = 2^X bytes, signed if X >= 3)
- 0x22: float (4 bytes)
- 0x23: double (8 bytes)
- 0x33: date (8-byte float, seconds since 2001-01-01 00:00:00 UTC)
- 0x4X: data/bytes (size from lower nibble, or if 0xF then read variable-length size)
- 0x5X: ASCII string
- 0x6X: Unicode string (UTF-16BE, byte count = size * 2)
- 0x8X: UID (size = X+1 bytes)
- 0xAX: array (count elements, then count object references)
- 0xCX: set (like array)
- 0xDX: dict (count key-value pairs, then key refs, then value refs)

When the lower nibble is 0xF (for types 0x4, 0x5, 0x6, 0xA, 0xC, 0xD), the actual size follows: read 1 byte, take lower 2 bits (m = byte & 0x3), then read 2^m bytes as a big-endian integer for the size.

Object references (in arrays, dicts, sets) are ref_size-byte big-endian integers that index into the offset table.

For JSON output, represent each type as:
- null → null
- bool → true/false
- integer → number (use integer if within safe range)
- float/double → number
- string → string
- data/bytes → base64-encoded string
- date → ISO 8601 string with Z suffix (e.g. "2024-06-15T12:30:00Z")
- array/set → JSON array
- dict → JSON object (keys are strings)
- UID → object: {"__uid__": <number>}

I've placed a sample file at `/app/input.plist` for you to test against. The output for this file should be structurally correct JSON matching the plist content.