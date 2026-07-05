# Technical Design Decisions & Architectural Trade-offs

This document details the engineering rationale, concurrency protections, database indexing strategy, and design trade-offs made for the **Distributed Job Scheduler**.

---

## 1. Concurrency Control & Claiming Strategy

### The Concurrency Limit Race Condition
In a distributed queue environment, a naive check-then-act query creates a classic race condition:
```sql
-- DANGER: Race condition if run concurrently
SELECT COUNT(*) FROM jobs WHERE queue_id = :qid AND status = 'running';
-- If count < max_concurrency, claim job...
```
Even with `FOR UPDATE SKIP LOCKED` on the candidate jobs, two workers under `READ COMMITTED` isolation level can simultaneously read a concurrency count of `max_concurrency - 1`, pass the check, and both claim a job, exceeding the queue's concurrency limit.

### The Solution: Transaction-Scoped Queue Locks
To eliminate this race condition without sacrificing performance across unrelated queues, we enforce **Transaction-Scoped Advisory Locks** on the queue ID:
1. When a worker is ready to poll, it first queries candidates of active, unpaused queues that have due jobs.
2. For a target queue, it enters a transaction and executes:
   ```sql
   SELECT pg_advisory_xact_lock(hashtext(:queue_id::text));
   ```
3. This blocks concurrent workers from claiming *from the same queue* simultaneously, serializing claiming transactions per queue.
4. Once locked, the count of currently running jobs is read, DAG parent dependencies are checked, and the next job is claimed atomically:
   ```sql
   UPDATE jobs SET status = 'claimed' ... WHERE id = (SELECT id ... FOR UPDATE SKIP LOCKED LIMIT 1)
   ```
5. On transaction commit or rollback, the lock is automatically released by PostgreSQL.

**Trade-off (Advisory Lock Key Space)**: `hashtext(UUID)` maps a 128-bit UUID to a 32-bit integer space. While hash collisions are statistically possible (which would briefly serialize claiming for two unrelated queues), this is accepted as a minor performance trade-off to leverage Postgres' built-in fast advisory lock system.

---

## 2. Scheduler Leader Election

### Pinned Connection for Session Locks
To prevent multiple horizontal API instances from running scheduled/recurring cron promotions redundantly, we employ leader election using a session-level advisory lock:
```sql
SELECT pg_try_advisory_lock(888888);
```
Since session-level advisory locks are bound to the specific PostgreSQL TCP connection, a shared connection pool (like `asyncpg` or `SQLAlchemy` pooled connections) would recycle, drop, or share the connection, causing leader state corruption. 
*   **Decision**: The scheduler service allocates its own dedicated, long-lived (pinned) connection at startup, keeping the lease active until the process terminates.

---

## 3. Recurring Jobs: Templates vs. Executions

A common anti-pattern is using the same table row to store a recurring job's schedule configuration and its execution state. This breaks because:
*   A recurring cron configuration never finishes, so status `completed` is meaningless.
*   Retrying a failed run would overwrite the schedule itself.

*   **Decision**: We split this into `job_templates` (stores configuration, `cron_expression`, `next_run_at`, status `active`/`paused`) and `jobs` (cloned execution instances). When the scheduler detects a template is due, it inserts a new execution row into `jobs` (referencing the template via `parent_template_id`) and updates `next_run_at` on the template.

---

## 4. Directed Acyclic Graph (DAG) Workflows

Instead of simple parent-child chains (which are limited to one parent per job), we support full Directed Acyclic Graphs (DAGs) using a join table:
```
job_dependencies (job_id, depends_on_job_id)
```
*   **Claim Check**: A child job is only claimable when:
    ```sql
    AND NOT EXISTS (
        SELECT 1 FROM job_dependencies jd
        JOIN jobs parent ON jd.depends_on_job_id = parent.id
        WHERE jd.job_id = j.id AND parent.status != 'completed'
    )
    ```
*   **Index Strategy**: We index both `job_id` (via the composite primary key) and create an explicit single-column index on `depends_on_job_id` to enable fast reverse-lookup queries when the dashboard displays downstream blocked jobs.
*   **Cycle Protection**: Cycle detection is enforced at the API layer. Before inserting a dependency `A depends_on B`, the API runs a depth-first search (DFS) traversing upstream from `B` to check if `A` is reachable. If it is, a circular dependency is detected and the request is rejected with HTTP 400.

---

## 5. Audit Logging & Cascade Deletion

To prevent data loss and preserve audit history (essential for distributed systems monitoring):
*   Deleting a project or queue does not cascade hard-delete jobs. Instead, we use soft-deletes (`deleted_at` timestamps) on `projects` and `queues`.
*   Deleting a job (if performed) uses `ON DELETE SET NULL` on the `job_executions` table. This keeps execution histories, logs, and timing metrics intact for historical system health analysis.

---

## 6. Job Batches & Atomic Counters

A batch represents a collection of jobs that run in parallel. A common concurrency bug is checking out a batch row, incrementing the progress counters in application code, and saving it back. Concurrent worker completions would cause lost updates.
*   **Decision**: We use PostgreSQL-level atomic increments for progress tracking:
    ```sql
    UPDATE job_batches 
    SET completed_jobs = completed_jobs + 1 
    WHERE id = :batch_id;
    ```
    Once all jobs in a batch are complete (i.e. `completed_jobs + failed_jobs == total_jobs`), the batch status is promoted to `completed` or `failed` accordingly.
