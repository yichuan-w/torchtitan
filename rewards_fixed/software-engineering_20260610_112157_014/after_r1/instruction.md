I have a raw disk image from a corrupted USB drive at `/app/disk.img`. Several files were accidentally deleted from this drive, and the deletion corrupted the allocation metadata, so standard recovery tools won't work.

I need you to recover EVERY deleted file you can find on this image. There may be deleted files scattered across the root directory and various subdirectories. Place all recovered files directly in `/app/recovered/` — do NOT recreate the original directory structure. Just put each recovered file in the top-level `/app/recovered/` directory. Use its original base filename when recoverable; when deletion erased filename characters, a reasonable reconstructed base filename is acceptable.

The files that were NOT deleted should be left alone — I only want the deleted ones.
