I need to build tmux 3.4 from source as a fully statically linked binary. The final binary should have NO dynamic library dependencies at all - it must work on any Linux x86_64 system without requiring any shared libraries.

Build everything in `/app/build` and install to `/opt/tmux-static`. The final `tmux` binary should be at `/opt/tmux-static/bin/tmux`.

Download the source tarballs from the official project pages:
- tmux 3.4
- libevent 2.1.12-stable
- ncurses 6.5

The binary size should be under 2.4MB after stripping. It must remain an ordinary uncompressed ELF; do not use executable packers or compression to meet the size limit.

Verify the build with:
1. `tmux -V` should output `tmux 3.4`
2. `ldd /opt/tmux-static/bin/tmux` should report "not a dynamic executable" or "statically linked"
