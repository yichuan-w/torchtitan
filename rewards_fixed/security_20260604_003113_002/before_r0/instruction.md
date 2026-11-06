A project at `/app/repo` has protected assets. The file `data/project_photo.jpg` contains hidden encrypted data. The encryption uses OpenSSL with a passphrase.

The passphrase is embedded in a binary resource file `data/noise.bin`. The file `config/keyloc.txt` contains the byte offset and length needed to extract it.

Recover the passphrase, extract and decrypt the hidden data, and write the final content to `/app/solution.txt`.
