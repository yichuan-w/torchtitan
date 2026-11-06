A security team intercepted an image file (`/app/image.png`) that is suspected to contain hidden data. The image appears to be a normal small PNG, but forensic analysis suggests that a secret flag has been embedded using LSB (Least Significant Bit) steganography.

Your task: extract the hidden flag from the image and write it (as a single line of ASCII text) to `/app/recovered_secret.txt`.

You have Python 3 and the Pillow imaging library available.
