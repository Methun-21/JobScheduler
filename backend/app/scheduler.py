import asyncio
import logging
from datetime import datetime, timezone
import asyncpg
from croniter import croniter
from sqlalchemy import select, update, insert
from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import JobTemplate, Job

logger = logging.getLogger("scheduler")
logging.basicConfig(level=logging.INFO)

class Scheduler:
    def __init__(self, check_interval_seconds: float = 5.0):
        self.check_interval = check_interval_seconds
        self.lock_id = 888888
        self.pinned_conn = None
        self.is_leader = False
        self.is_running = False

    async def start(self):
        self.is_running = True
        asyncio.create_task(self.run())

    async def stop(self):
        self.is_running = False
        if self.pinned_conn:
            try:
                # Release the session lock explicitly
                await self.pinned_conn.execute("SELECT pg_advisory_unlock($1);", self.lock_id)
                await self.pinned_conn.close()
                logger.info("Scheduler leader connection closed and lock released.")
            except Exception as e:
                logger.error(f"Error closing leader connection: {e}")
            finally:
                self.pinned_conn = None
                self.is_leader = False

    async def run(self):
        logger.info("Scheduler background service initialized.")
        while self.is_running:
            try:
                if not self.is_leader:
                    await self._attempt_leadership()
                
                if self.is_leader:
                    await self._promote_templates_and_jobs()
                
            except Exception as e:
                logger.error(f"Error in scheduler execution loop: {e}")
                self.is_leader = False
                if self.pinned_conn:
                    try:
                        await self.pinned_conn.close()
                    except Exception:
                        pass
                    self.pinned_conn = None
            
            await asyncio.sleep(self.check_interval)

    async def _attempt_leadership(self):
        """Tries to acquire the advisory lock on a pinned connection."""
        try:
            logger.info("Attempting to acquire scheduler leadership lock...")
            self.pinned_conn = await asyncpg.connect(
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                database=settings.DB_NAME
            )
            
            # Try to acquire session-level advisory lock
            row = await self.pinned_conn.fetchrow("SELECT pg_try_advisory_lock($1) as acquired;", self.lock_id)
            if row and row['acquired']:
                self.is_leader = True
                logger.info("Scheduler leadership lock ACQUIRED. Running as leader.")
            else:
                self.is_leader = False
                logger.info("Scheduler leadership lock is held by another instance. Standing by...")
                await self.pinned_conn.close()
                self.pinned_conn = None
        except Exception as e:
            logger.error(f"Failed to connect or lock database for scheduler: {e}")
            self.is_leader = False
            self.pinned_conn = None

    async def _promote_templates_and_jobs(self):
        """Scans for active due templates and scheduled jobs, promoting them to queued."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 1. Handle recurring templates
                # Fetch templates where next_run_at <= now
                stmt_templates = select(JobTemplate).where(
                    JobTemplate.status == "active",
                    JobTemplate.next_run_at <= now
                )
                result = await session.execute(stmt_templates)
                due_templates = result.scalars().all()
                
                for template in due_templates:
                    # Create job instance
                    job_id = uuid_generator()
                    # Calculate next run time using croniter
                    cron = croniter(template.cron_expression, now)
                    next_run = cron.get_next(datetime)
                    # Convert to timezone aware if native
                    if next_run.tzinfo is None:
                        next_run = next_run.replace(tzinfo=timezone.utc)
                    
                    # Insert execution job
                    await session.execute(
                        insert(Job).values(
                            id=job_id,
                            queue_id=template.queue_id,
                            parent_template_id=template.id,
                            type=template.type,
                            payload=template.payload,
                            status="queued",
                            priority=0,
                            run_at=now,
                            created_at=now,
                            updated_at=now
                        )
                    )
                    
                    # Update template
                    template.next_run_at = next_run
                    template.updated_at = now
                    logger.info(f"Promoted template '{template.name}' ({template.id}) -> job {job_id}. Next run at {next_run}")
                
                # 2. Handle scheduled single jobs
                # Fetch jobs where status = 'scheduled' and run_at <= now
                stmt_jobs = select(Job).where(
                    Job.status == "scheduled",
                    Job.run_at <= now
                )
                res_jobs = await session.execute(stmt_jobs)
                due_jobs = res_jobs.scalars().all()
                
                for job in due_jobs:
                    job.status = "queued"
                    job.updated_at = now
                    logger.info(f"Promoted scheduled job '{job.id}' to queued.")
                
                await session.commit()

def uuid_generator():
    import uuid
    return uuid.uuid4()

scheduler_instance = Scheduler()
