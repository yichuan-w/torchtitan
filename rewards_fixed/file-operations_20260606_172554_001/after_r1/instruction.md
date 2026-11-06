I have a corrupted binary archive at `/app/data.blob` that was produced by our internal data processing pipeline. It should contain several files, but a storage glitch corrupted parts of its metadata and file data.

Standard archiving tools (tar, zip, etc.) won't help — this is a custom format our pipeline uses. The format is simple: the archive is just file entries concatenated together, each with a small binary header followed by the raw file data, and a trailing integrity checksum.

I need you to recover all files from this blob. Create a directory `/app/recovered/` and place each recovered file there with its original filename whenever possible. If a file's name was lost to corruption, use any reasonable placeholder name.

The blob is about 1.6 KB, so you can examine it with hexdump tools to understand the format. Use the archive's integrity checks to help distinguish damaged or padded data from the intended file contents.
