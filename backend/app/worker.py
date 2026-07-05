import os
import sys

# Support executing worker.py directly as a script by adding project root to sys.path
backend_app_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(os.path.dirname(backend_app_dir))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

import asyncio
import logging
import socket
import signal
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import uuid
from sqlalchemy import select, update, insert, func, text
from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Worker, Queue, Job, JobDependency, JobExecution, JobLog, JobBatch, RetryPolicy
from backend.app.job_handlers import execute_job

logger = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)

class WorkerDaemon:
    def __init__(self, max_local_concurrency: int = 5, poll_interval: float = 1.0):
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self.hostname = socket.gethostname()
        self.max_concurrency = max_local_concurrency
        self.poll_interval = poll_interval
        self.semaphore = asyncio.Semaphore(max_local_concurrency)
        self.is_running = False
        self.active_tasks = set()
        self.loop = None

    async def start(self):
        self.is_running = True
        self.loop = asyncio.get_running_loop()
        
        # Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown(sig)))
            except NotImplementedError:
                # Signal handlers are not fully supported on some platforms (e.g. Windows in older python versions, but usually fine)
                pass

        # Register worker in database
        await self._register_worker()
        
        # Start heartbeat background loop
        asyncio.create_task(self._heartbeat_loop())
        
        # Start polling loop
        logger.info(f"Worker {self.worker_id} started. Max concurrency: {self.max_concurrency}")
        await self._poll_loop()

    async def shutdown(self, sig=None):
        if sig:
            logger.info(f"Received signal {sig.name if hasattr(sig, 'name') else sig}. Initiating graceful shutdown...")
        else:
            logger.info("Initiating graceful shutdown...")
            
        self.is_running = False
        
        # Unregister worker or set offline
        await self._set_worker_status("offline", None)
        
        # Wait for active tasks to complete
        if self.active_tasks:
            logger.info(f"Waiting for {len(self.active_tasks)} active jobs to complete...")
            await asyncio.gather(*self.active_tasks, return_exceptions=True)
            
        logger.info("Worker shutdown complete. Exiting.")
        sys.exit(0)

    async def _register_worker(self):
        """Registers the worker in the DB at startup."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Check if exists
                stmt = select(Worker).where(Worker.id == self.worker_id)
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                
                if existing:
                    existing.status = "idle"
                    existing.last_heartbeat_at = now
                    existing.current_job_id = None
                else:
                    session.add(
                        Worker(
                            id=self.worker_id,
                            hostname=self.hostname,
                            status="idle",
                            registered_at=now,
                            last_heartbeat_at=now
                        )
                    )
                await session.commit()
        logger.info(f"Worker registered in database: {self.worker_id}")

    async def _set_worker_status(self, status: str, current_job_id: Optional[uuid.UUID]):
        """Helper to update worker status in DB."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(Worker)
                    .where(Worker.id == self.worker_id)
                    .values(status=status, last_heartbeat_at=now, current_job_id=current_job_id)
                )
                await session.commit()

    async def _heartbeat_loop(self):
        """Periodically updates the worker's heartbeat in the database."""
        while self.is_running:
            try:
                now = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        await session.execute(
                            update(Worker)
                            .where(Worker.id == self.worker_id)
                            .values(last_heartbeat_at=now)
                        )
                        await session.commit()
            except Exception as e:
                logger.error(f"Heartbeat update failed: {e}")
            await asyncio.sleep(5)

    async def _poll_loop(self):
        """Continuous polling of queues for due jobs."""
        while self.is_running:
            # Calculate currently available slots
            available_slots = self.semaphore._value
            if available_slots <= 0:
                # Worker is busy, wait for task completions
                await asyncio.sleep(self.poll_interval)
                continue
                
            try:
                # Step 1: Find candidate queues that are active and have queued jobs
                queue_id = await self._find_candidate_queue()
                if not queue_id:
                    # No queues have jobs or queues are paused/busy
                    await asyncio.sleep(self.poll_interval)
                    continue
                
                # Step 2: Attempt to claim job from the candidate queue using advisory locking
                job = await self._claim_job_from_queue(queue_id)
                if job:
                    # Spawn job execution task
                    task = asyncio.create_task(self._execute_job_task(job))
                    self.active_tasks.add(task)
                    task.add_done_callback(self.active_tasks.discard)
                else:
                    # No job claimed from this queue (concurrency limit hit or race condition)
                    await asyncio.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Error in poll loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _find_candidate_queue(self) -> Optional[uuid.UUID]:
        """Queries for queues that have queued jobs waiting to run."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            # Fetch active, unpaused queues with queued jobs whose run_at <= now
            # Order by queue priority desc
            stmt = (
                select(Queue.id)
                .join(Job, Job.queue_id == Queue.id)
                .where(
                    Queue.is_paused == False,
                    Queue.deleted_at == None,
                    Job.status == "queued",
                    (Job.run_at == None) | (Job.run_at <= now)
                )
                .order_by(Queue.priority.desc())
                .limit(1)
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def _claim_job_from_queue(self, queue_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Claims a single job from a specific queue using a transaction-level advisory lock."""
        now = datetime.now(timezone.utc)
        
        async with AsyncSessionLocal() as session:
            # We must use raw connection execution or ensure it's in a single transaction
            async with session.begin():
                # 1. Acquire transaction-level advisory lock on queue id hash
                # We convert UUID to string, get its hash, and use pg_advisory_xact_lock
                queue_hash = hash(str(queue_id)) & 0x7FFFFFFF  # Force positive 32-bit int
                await session.execute(text("SELECT pg_advisory_xact_lock(:qhash)"), {"qhash": queue_hash})
                
                # 2. Get queue properties
                stmt_queue = select(Queue).where(Queue.id == queue_id, Queue.is_paused == False).with_for_update()
                res_q = await session.execute(stmt_queue)
                queue = res_q.scalar_one_or_none()
                if not queue:
                    return None
                    
                # 3. Check concurrency limit
                # Count jobs currently 'running' or 'claimed' in this queue
                stmt_count = select(func.count(Job.id)).where(
                    Job.queue_id == queue_id,
                    Job.status.in_(["running", "claimed"])
                )
                res_count = await session.execute(stmt_count)
                running_count = res_count.scalar() or 0
                
                if running_count >= queue.max_concurrency:
                    # Concurrency limit reached for this queue
                    return None
                    
                # 4. Find the next job in queue order (priority desc, run_at asc)
                # Verify DAG dependencies: parents must be completed
                from sqlalchemy.orm import aliased
                ParentJob = aliased(Job)
                
                stmt_job = (
                    select(Job)
                    .where(
                        Job.queue_id == queue_id,
                        Job.status == "queued",
                        (Job.run_at == None) | (Job.run_at <= now),
                        # Check dependencies: no parent job exists with status != 'completed'
                        ~select(JobDependency).join(
                            ParentJob, ParentJob.id == JobDependency.depends_on_job_id
                        ).where(
                            JobDependency.job_id == Job.id,
                            ParentJob.status != "completed"
                        ).exists()
                    )
                    .order_by(Job.priority.desc(), Job.run_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                
                res_j = await session.execute(stmt_job)
                job = res_j.scalar_one_or_none()
                
                if not job:
                    return None
                    
                # 5. Claim the job
                job.status = "claimed"
                job.updated_at = now
                
                # Fetch retry policy overrides if any, or default queue policy
                policy_id = job.retry_policy_id or queue.default_retry_policy_id
                max_attempts = job.max_attempts
                if policy_id:
                    stmt_policy = select(RetryPolicy.max_retries).where(RetryPolicy.id == policy_id)
                    res_p = await session.execute(stmt_policy)
                    max_retries = res_p.scalar()
                    if max_retries is not None:
                        max_attempts = max_retries + 1
                
                # Store job details in local dict to return
                claimed_job_data = {
                    "id": job.id,
                    "queue_id": job.queue_id,
                    "type": job.type,
                    "payload": job.payload,
                    "attempt_count": job.attempt_count,
                    "max_attempts": max_attempts,
                    "retry_policy_id": policy_id,
                    "batch_id": job.batch_id
                }
                
                await session.commit()
                return claimed_job_data

    async def _execute_job_task(self, job_data: Dict[str, Any]):
        """Worker task executing the claimed job logic."""
        job_id = job_data["id"]
        
        async with self.semaphore:
            # 1. Update status to 'running' and worker state
            await self._set_worker_status("busy", job_id)
            
            now = datetime.now(timezone.utc)
            execution_id = uuid.uuid4()
            attempt = job_data["attempt_count"] + 1
            
            logger.info(f"Worker {self.worker_id} executing job {job_id} (Attempt {attempt}/{job_data['max_attempts']})")
            
            # Write JobExecution entry in DB
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(
                        JobExecution(
                            id=execution_id,
                            job_id=job_id,
                            worker_id=self.worker_id,
                            attempt_number=attempt,
                            status="running",
                            started_at=now
                        )
                    )
                    # Update job status to 'running'
                    await session.execute(
                        update(Job)
                        .where(Job.id == job_id)
                        .values(status="running", attempt_count=attempt, updated_at=now)
                    )
                    await session.commit()

            # Logging callback helper
            async def log_cb(level: str, message: str):
                logger.info(f"[{job_id} - {level.upper()}]: {message}")
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        session.add(
                            JobLog(
                                id=uuid.uuid4(),
                                execution_id=execution_id,
                                timestamp=datetime.now(timezone.utc),
                                level=level,
                                message=message
                            )
                        )
                        await session.commit()

            # 2. Run execution
            result = None
            error_msg = None
            status = "completed"
            
            try:
                await log_cb("info", f"Starting execution of job type '{job_data['type']}'")
                result = await execute_job(job_data["type"], job_data["payload"], log_cb)
                await log_cb("info", "Job execution completed successfully.")
            except Exception as e:
                status = "failed"
                error_msg = str(e)
                await log_cb("error", f"Job execution failed with exception: {error_msg}")
                logger.error(f"Job {job_id} failed: {error_msg}")

            # 3. Post-execution status updates
            finished_at = datetime.now(timezone.utc)
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Update JobExecution
                    await session.execute(
                        update(JobExecution)
                        .where(JobExecution.id == execution_id)
                        .values(status=status, finished_at=finished_at, error_message=error_msg, result=result)
                    )
                    
                    if status == "completed":
                        # Mark job completed
                        await session.execute(
                            update(Job)
                            .where(Job.id == job_id)
                            .values(status="completed", updated_at=finished_at)
                        )
                        
                        # Handle batch increment atomically
                        if job_data["batch_id"]:
                            await session.execute(
                                update(JobBatch)
                                .where(JobBatch.id == job_data["batch_id"])
                                .values(completed_jobs=JobBatch.completed_jobs + 1)
                            )
                            # Check batch completion
                            await self._check_batch_completion(session, job_data["batch_id"])
                            
                    else:  # failed
                        # Check retry policies
                        if attempt < job_data["max_attempts"]:
                            # Calculate backoff delay
                            delay = await self._calculate_backoff_delay(session, job_data["retry_policy_id"], attempt)
                            run_at = finished_at + timedelta(seconds=delay)
                            
                            await session.execute(
                                update(Job)
                                .where(Job.id == job_id)
                                .values(status="queued", run_at=run_at, updated_at=finished_at)
                            )
                            await log_cb("info", f"Job rescheduled for retry attempt {attempt + 1} at {run_at} (delay {delay}s)")
                        else:
                            # Move to Dead Letter
                            await session.execute(
                                update(Job)
                                .where(Job.id == job_id)
                                .values(status="dead_letter", updated_at=finished_at)
                            )
                            await log_cb("error", "Max retry attempts exhausted. Job moved to dead_letter queue.")
                            
                            # Handle batch failure increment atomically
                            if job_data["batch_id"]:
                                await session.execute(
                                    update(JobBatch)
                                    .where(JobBatch.id == job_data["batch_id"])
                                    .values(failed_jobs=JobBatch.failed_jobs + 1)
                                )
                                await self._check_batch_completion(session, job_data["batch_id"])
                                
                    await session.commit()
            
            # Reset worker status to idle
            await self._set_worker_status("idle", None)

    async def _calculate_backoff_delay(self, session, policy_id: Optional[uuid.UUID], attempt: int) -> int:
        """Calculates backoff delay based on the RetryPolicy definition."""
        if not policy_id:
            # Default fall-back: fixed delay of 5 seconds
            return 5
            
        stmt = select(RetryPolicy).where(RetryPolicy.id == policy_id)
        res = await session.execute(stmt)
        policy = res.scalar_one_or_none()
        
        if not policy:
            return 5
            
        strategy = policy.strategy
        base_delay = policy.base_delay_seconds
        max_delay = policy.max_delay_seconds
        
        if strategy == "fixed":
            delay = base_delay
        elif strategy == "linear":
            delay = base_delay * attempt
        elif strategy == "exponential":
            delay = base_delay * (2 ** (attempt - 1))
        else:
            delay = base_delay
            
        if max_delay is not None:
            delay = min(delay, max_delay)
            
        return int(delay)

    async def _check_batch_completion(self, session, batch_id: uuid.UUID):
        """Updates batch status if all jobs are finished."""
        # Query total, completed, and failed counts
        stmt = select(JobBatch).where(JobBatch.id == batch_id).with_for_update()
        res = await session.execute(stmt)
        batch = res.scalar_one_or_none()
        
        if batch:
            total = batch.total_jobs
            done = batch.completed_jobs + batch.failed_jobs
            
            if done >= total:
                # Batch is complete
                # Status is 'failed' if any job failed, otherwise 'completed'
                new_status = "failed" if batch.failed_jobs > 0 else "completed"
                batch.status = new_status
                logger.info(f"Batch '{batch.name}' ({batch_id}) finished with status: {new_status} ({batch.completed_jobs} completed, {batch.failed_jobs} failed)")

if __name__ == "__main__":
    # Standard script run
    daemon = WorkerDaemon()
    asyncio.run(daemon.start())
