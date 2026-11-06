#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>

#define BUFSIZE 4096

static int port = 9000;
static char root_dir[256] = "/www";

void handle(int fd) {
    char buf[BUFSIZE];
    int n = read(fd, buf, BUFSIZE - 1);
    if (n <= 0) return;
    buf[n] = '\0';

    // Parse GET request: GET /path HTTP/1.x
    char method[16], path[256];
    if (sscanf(buf, "%15s %255s", method, path) < 2) return;
    if (strcmp(method, "GET") != 0) {
        dprintf(fd, "HTTP/1.0 405 Method Not Allowed\r\n\r\n");
        return;
    }

    // Safety: prevent directory traversal
    if (strstr(path, "..")) {
        dprintf(fd, "HTTP/1.0 403 Forbidden\r\n\r\n");
        return;
    }

    // Default to index.html
    if (strcmp(path, "/") == 0) strcpy(path, "/index.html");

    // Build full path relative to root_dir (we already chdir'd)
    char fullpath[512];
    snprintf(fullpath, sizeof(fullpath), ".%s", path);

    int file = open(fullpath, O_RDONLY);
    if (file < 0) {
        dprintf(fd, "HTTP/1.0 404 Not Found\r\n\r\n");
        return;
    }

    dprintf(fd, "HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n");

    char fbuf[BUFSIZE];
    int r;
    while ((r = read(file, fbuf, BUFSIZE)) > 0) {
        write(fd, fbuf, r);
    }
    close(file);
}

int main(int argc, char *argv[]) {
    signal(SIGCHLD, SIG_IGN);
    signal(SIGPIPE, SIG_IGN);

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
            port = atoi(argv[++i]);
        else if (strcmp(argv[i], "-r") == 0 && i + 1 < argc)
            strncpy(root_dir, argv[++i], sizeof(root_dir) - 1);
    }

    if (chdir(root_dir) < 0) {
        fprintf(stderr, "Cannot chdir to %s: %s\n", root_dir, strerror(errno));
        return 1;
    }

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return 1; }

    int opt = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    if (bind(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind"); return 1;
    }
    if (listen(sock, 10) < 0) { perror("listen"); return 1; }

    printf("miniserve listening on port %d, root=%s\n", port, root_dir);
    fflush(stdout);

    // Open /dev/null for logging (requires /dev/null in chroot)
    int devnull = open("/dev/null", O_WRONLY);
    if (devnull < 0) {
        fprintf(stderr, "Warning: cannot open /dev/null: %s\n", strerror(errno));
    }

    while (1) {
        int client = accept(sock, NULL, NULL);
        if (client < 0) continue;
        if (fork() == 0) {
            close(sock);
            handle(client);
            close(client);
            exit(0);
        }
        close(client);
    }
    return 0;
}
