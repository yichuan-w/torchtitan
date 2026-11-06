During a security incident investigation, password hashes were recovered at `/app/leaked_hashes.txt`. They use standard Unix crypt formats. John the Ripper is installed.

A wordlist at `/app/wordlist.txt` may help, but note that a password may be a mutated variant not appearing verbatim in the wordlist (e.g., leet-speak). You may need to use John's rule-based mangling to recover all three passwords.

Save all cracked passwords to `/app/cracked_passwords.txt`, one per line in `user:password` format.
