The PNG files in /app/corrupted/ cannot be opened by any image viewer or library. They were accidentally corrupted during a failed Git operation. The exact nature of the corruption is unknown — the files were committed and pushed, then later discovered to be broken.

Recover the original, uncorrupted PNG files and place them in /app/recovered/ with the same filenames. Each recovered file must be a valid PNG that opens correctly and renders the original image content.

The recovered PNG encoding and ancillary metadata may differ from the originals, but the dimensions and decoded pixels must match exactly.
