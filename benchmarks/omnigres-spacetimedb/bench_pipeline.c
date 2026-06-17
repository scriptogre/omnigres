/*
 * Pipelined Postgres benchmark using libpq pipeline mode.
 * Matches SpacetimeDB: N threads x M queries per pipeline batch.
 * Zero overhead: C threads, no GIL, no runtime.
 *
 * Compile: cc -O2 -o bench_pipeline bench_pipeline.c \
 *   -I$(pg_config --includedir) -L$(pg_config --libdir) -lpq -lpthread
 *
 * Usage: ./bench_pipeline -w 64 -b 40 -s 60 -a 0 -p 5435 -d benchdb
 */
#include <libpq-fe.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>

#define ACCOUNTS 100000

typedef struct {
    int id;
    const char *conninfo;
    int batch_size;
    int seconds;
    double alpha;
    long completed;
} worker_args;

static double get_time(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* Simple Zipfian sampler (precomputed CDF would be faster but this works) */
static int zipf_pick(int n, double alpha) {
    /* Rejection sampling */
    double sum = 0;
    for (int i = 1; i <= n; i++) sum += 1.0 / pow(i, alpha);
    double r = ((double)rand() / RAND_MAX) * sum;
    double acc = 0;
    for (int i = 1; i <= n; i++) {
        acc += 1.0 / pow(i, alpha);
        if (acc >= r) return i;
    }
    return n;
}

static void pick_pair(double alpha, int *f, int *t) {
    if (alpha == 0) {
        *f = rand() % ACCOUNTS + 1;
        *t = rand() % ACCOUNTS + 1;
        while (*t == *f) *t = rand() % ACCOUNTS + 1;
    } else {
        *f = zipf_pick(ACCOUNTS, alpha);
        *t = zipf_pick(ACCOUNTS, alpha);
        while (*t == *f) *t = zipf_pick(ACCOUNTS, alpha);
    }
}

static void *worker(void *arg) {
    worker_args *a = (worker_args *)arg;
    a->completed = 0;
    srand(time(NULL) ^ (a->id * 31));

    PGconn *conn = PQconnectdb(a->conninfo);
    if (PQstatus(conn) != CONNECTION_OK) {
        fprintf(stderr, "worker %d: connect failed: %s\n",
                a->id, PQerrorMessage(conn));
        PQfinish(conn);
        return NULL;
    }

    /* Prepare the statement once */
    PGresult *prep = PQprepare(conn, "xfer",
        "SELECT transfer($1::int, $2::int, $3::bigint)", 3, NULL);
    if (PQresultStatus(prep) != PGRES_COMMAND_OK) {
        fprintf(stderr, "worker %d: prepare failed: %s\n",
                a->id, PQresultErrorMessage(prep));
        PQclear(prep);
        PQfinish(conn);
        return NULL;
    }
    PQclear(prep);

    double end_time = get_time() + a->seconds;

    if (PQenterPipelineMode(conn) != 1) {
        fprintf(stderr, "worker %d: pipeline mode failed\n", a->id);
        PQfinish(conn);
        return NULL;
    }

    while (get_time() < end_time) {
        /* Send batch of queries */
        for (int i = 0; i < a->batch_size; i++) {
            int f, t;
            pick_pair(a->alpha, &f, &t);
            int amt = rand() % 1000 + 1;

            char sf[12], st[12], sa[12];
            snprintf(sf, sizeof(sf), "%d", f);
            snprintf(st, sizeof(st), "%d", t);
            snprintf(sa, sizeof(sa), "%d", amt);
            const char *vals[3] = {sf, st, sa};

            if (!PQsendQueryPrepared(conn, "xfer", 3, vals, NULL, NULL, 0)) {
                fprintf(stderr, "worker %d: send failed: %s\n",
                        a->id, PQerrorMessage(conn));
                goto done;
            }
        }

        if (!PQpipelineSync(conn)) {
            fprintf(stderr, "worker %d: sync failed\n", a->id);
            break;
        }

        /* Collect results for this batch */
        int batch_ok = 0;
        for (int i = 0; i < a->batch_size; i++) {
            PGresult *res = PQgetResult(conn);
            if (!res) break;
            ExecStatusType st = PQresultStatus(res);
            PQclear(res);

            if (st == PGRES_TUPLES_OK || st == PGRES_COMMAND_OK) {
                batch_ok++;
                /* Read NULL separator */
                res = PQgetResult(conn);
                if (res) PQclear(res);
            } else {
                /* Error (deadlock etc): drain rest of pipeline segment */
                while ((res = PQgetResult(conn)) != NULL) PQclear(res);
                break;
            }
        }

        /* Read sync marker */
        PGresult *res = PQgetResult(conn);
        if (res) PQclear(res);

        a->completed += batch_ok;
    }
done:
    PQexitPipelineMode(conn);

    PQfinish(conn);
    return NULL;
}

int main(int argc, char **argv) {
    int workers = 64, batch = 40, seconds = 60, port = 5435;
    double alpha = 0;
    const char *db = "benchdb", *user = NULL, *host = "127.0.0.1";

    /* ponytail: getopt is fine, no argparse lib needed */
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-w") && i+1 < argc) workers = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-b") && i+1 < argc) batch = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i+1 < argc) seconds = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-a") && i+1 < argc) alpha = atof(argv[++i]);
        else if (!strcmp(argv[i], "-p") && i+1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-d") && i+1 < argc) db = argv[++i];
        else if (!strcmp(argv[i], "-U") && i+1 < argc) user = argv[++i];
        else if (!strcmp(argv[i], "-h") && i+1 < argc) host = argv[++i];
    }

    if (!user) user = getenv("USER");
    if (!user) user = "postgres";

    char conninfo[512];
    snprintf(conninfo, sizeof(conninfo),
             "host=%s port=%d dbname=%s user=%s", host, port, db, user);

    printf("[bench] %d workers x %d batch, alpha=%.1f, %ds\n",
           workers, batch, alpha, seconds);

    pthread_t *threads = calloc(workers, sizeof(pthread_t));
    worker_args *args = calloc(workers, sizeof(worker_args));

    double t0 = get_time();
    for (int i = 0; i < workers; i++) {
        args[i] = (worker_args){i, conninfo, batch, seconds, alpha, 0};
        pthread_create(&threads[i], NULL, worker, &args[i]);
    }
    for (int i = 0; i < workers; i++) pthread_join(threads[i], NULL);
    double elapsed = get_time() - t0;

    long total = 0;
    for (int i = 0; i < workers; i++) total += args[i].completed;

    printf("[bench] completed = %ld, elapsed = %.2fs, tps = %.0f\n",
           total, elapsed, (double)total / seconds);

    free(threads);
    free(args);
    return 0;
}
