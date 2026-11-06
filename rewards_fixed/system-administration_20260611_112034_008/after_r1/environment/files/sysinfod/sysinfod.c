/*
 * sysinfod - Simple System Information HTTP Daemon
 *
 * Serves system information as JSON over HTTP on a configurable port.
 * Reads configuration from /etc/sysinfod.conf.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/utsname.h>
#include <sys/sysinfo.h>
#include <time.h>
#include <errno.h>
#include <pthread.h>
#include <curl/curl.h>

#define DEFAULT_PORT 9090
#define MAX_CONF_LINE 256
#define BACKLOG 16
#define BUFSIZE 4096

static volatile sig_atomic_t running = 1;
static int listen_port = DEFAULT_PORT;
static int debug_mode = 0;  /* reserved for future use */

static void handle_signal(int sig) {
    (void)sig;
    running = 0;
}

static int read_config(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "Error: cannot open config %s: %s\n",
                path, strerror(errno));
        return -1;
    }
    char line[MAX_CONF_LINE];
    while (fgets(line, sizeof(line), f)) {
        char *p = strchr(line, '#');
        if (p) *p = '\0';
        p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0' || *p == '\n') continue;
        char key[128], val[128];
        if (sscanf(p, "%127[^=]=%127s", key, val) == 2) {
            if (strcmp(key, "port") == 0) {
                listen_port = atoi(val);
            }
        }
    }
    fclose(f);
    return 0;
}

static void build_json_response(char *buf, size_t bufsize) {
    struct sysinfo si;
    struct utsname uts;
    char hostname[256];

    sysinfo(&si);
    uname(&uts);
    gethostname(hostname, sizeof(hostname));

    long uptime_sec = si.uptime;
    long days = uptime_sec / 86400;
    long hours = (uptime_sec % 86400) / 3600;
    long mins = (uptime_sec % 3600) / 60;
    long secs = uptime_sec % 60;

    snprintf(buf, bufsize,
        "{"
        "\"hostname\":\"%s\","
        "\"kernel\":\"%s\","
        "\"arch\":\"%s\","
        "\"uptime_sec\":%ld,"
        "\"uptime_str\":\"%ldd %ldh %ldm %lds\","
        "\"load_1m\":%.2f,"
        "\"load_5m\":%.2f,"
        "\"load_15m\":%.2f,"
        "\"total_ram_mb\":%lu,"
        "\"free_ram_mb\":%lu,"
        "\"procs\":%u,"
        "\"server_version\":\"1.0.0\""
        "}",
        hostname,
        uts.release,
        uts.machine,
        uptime_sec,
        days, hours, mins, secs,
        si.loads[0] / 65536.0,
        si.loads[1] / 65536.0,
        si.loads[2] / 65536.0,
        si.totalram * si.mem_unit / (1024 * 1024),
        si.freeram * si.mem_unit / (1024 * 1024),
        si.procs
    );
}

static void *handle_client(void *arg) {
    int client_fd = *(int *)arg;
    free(arg);

    char buf[BUFSIZE];
    ssize_t n = recv(client_fd, buf, sizeof(buf) - 1, 0);
    if (n <= 0) {
        close(client_fd);
        return NULL;
    }
    buf[n] = '\0';

    char json[2048];
    build_json_response(json, sizeof(json));

    char response[BUFSIZE];
    int resp_len = snprintf(response, sizeof(response),
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s",
        strlen(json), json);

    send(client_fd, response, resp_len, 0);
    close(client_fd);
    return NULL;
}

int main(int argc, char **argv) {
    const char *conf_path = "/etc/sysinfod.conf";
    if (argc > 1) conf_path = argv[1];

    if (read_config(conf_path) != 0) {
        fprintf(stderr, "sysinfod: failed to read config, exiting\n");
        return 1;
    }

    signal(SIGTERM, handle_signal);
    signal(SIGINT, handle_signal);

    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        return 1;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(listen_port);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(server_fd);
        return 1;
    }

    if (listen(server_fd, BACKLOG) < 0) {
        perror("listen");
        close(server_fd);
        return 1;
    }

    printf("sysinfod: listening on port %d\n", listen_port);
    fflush(stdout);

    /* Verify curl is available */
    CURL *curl = curl_easy_init();
    if (curl) {
        printf("sysinfod: libcurl initialized successfully\n");
        curl_easy_cleanup(curl);
    } else {
        fprintf(stderr, "sysinfod: warning: libcurl init failed\n");
    }

    while (running) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        int client_fd = accept(server_fd, (struct sockaddr *)&client_addr, &client_len);
        if (client_fd < 0) {
            if (errno == EINTR && !running) break;
            perror("accept");
            continue;
        }

        int *fd_arg = malloc(sizeof(int));
        *fd_arg = client_fd;
        pthread_t thread;
        pthread_create(&thread, NULL, handle_client, fd_arg);
        pthread_detach(thread);
    }

    printf("sysinfod: shutting down\n");
    close(server_fd);
    return 0;
}
