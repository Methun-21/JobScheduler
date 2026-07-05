from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from uuid import UUID
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert, func
from backend.app.database import get_db
from backend.app.models import User, Queue, Job, JobDependency, JobBatch, JobExecution, JobLog, Project, OrgMember
from backend.app.schemas import JobCreate, JobOut, JobBatchCreate, JobBatchOut, JobExecutionOut, JobLogOut
from backend.app.routers.auth import get_current_user
from backend.app.routers.queues import verify_project_access

router = APIRouter(tags=["jobs"])

# Helper for queue authorization
async def verify_queue_access(queue_id: UUID, user_id: UUID, db: AsyncSession, roles=None):
    stmt = select(Queue).where(Queue.id == queue_id, Queue.deleted_at == None)
    res = await db.execute(stmt)
    queue = res.scalar_one_or_none()
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")
    await verify_project_access(queue.project_id, user_id, db, roles)
    return queue

# Helper to detect cycles in job dependencies (DFS check)
async def detect_cycle(child_id: UUID, parent_id: UUID, db: AsyncSession) -> bool:
    """Returns True if adding 'child depends on parent' would introduce a cycle."""
    # We trace upstream: can we reach child_id from parent_id?
    visited = set()
    stack = [parent_id]
    
    while stack:
        curr = stack.pop()
        if curr == child_id:
            return True
        if curr in visited:
            continue
        visited.add(curr)
        
        # Get all parent dependencies of current job (what does curr depend on?)
        stmt = select(JobDependency.depends_on_job_id).where(JobDependency.job_id == curr)
        res = await db.execute(stmt)
        parents = res.scalars().all()
        stack.extend(parents)
        
    return False

# --- Job Submission ---

@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def submit_job(
    job_in: JobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    queue = await verify_queue_access(job_in.queue_id, current_user.id, db)
    
    # Check idempotency key unique constraint
    if job_in.idempotency_key:
        stmt_idem = select(Job).where(
            Job.queue_id == job_in.queue_id,
            Job.idempotency_key == job_in.idempotency_key
        )
        res_idem = await db.execute(stmt_idem)
        existing = res_idem.scalar_one_or_none()
        if existing:
            return existing  # Return existing job for idempotency

    now = datetime.now(timezone.utc)
    
    # Determine initial status
    status_val = "queued"
    if job_in.run_at and job_in.run_at > now:
        status_val = "scheduled"
        
    # Generate ID upfront so we can bind dependencies
    job_id = uuid.uuid4()
    
    job = Job(
        id=job_id,
        queue_id=job_in.queue_id,
        type=job_in.type,
        payload=job_in.payload,
        priority=job_in.priority,
        status=status_val,
        run_at=job_in.run_at,
        retry_policy_id=job_in.retry_policy_id,
        max_attempts=job_in.max_attempts,
        idempotency_key=job_in.idempotency_key
    )
    db.add(job)
    
    # Set dependencies if provided
    if job_in.dependencies:
        for dep_id in job_in.dependencies:
            # Verify dependency exists
            stmt_dep_check = select(Job.id).where(Job.id == dep_id)
            res_dep_check = await db.execute(stmt_dep_check)
            if not res_dep_check.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"Dependency job {dep_id} does not exist")
                
            # Since job is new, adding parent dependency can't create a cycle (it has no children yet)
            db.add(JobDependency(job_id=job_id, depends_on_job_id=dep_id))
            
    await db.commit()
    await db.refresh(job)
    return job

# --- Batch Submission ---

@router.post("/batches", response_model=JobBatchOut, status_code=status.HTTP_201_CREATED)
async def submit_batch(
    batch_in: JobBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    await verify_project_access(batch_in.project_id, current_user.id, db, roles=["owner", "admin", "member"])
    
    if not batch_in.jobs:
        raise HTTPException(status_code=400, detail="Batch must contain at least one job.")
        
    batch_id = uuid.uuid4()
    total_jobs = len(batch_in.jobs)
    
    # 1. Create JobBatch record
    batch = JobBatch(
        id=batch_id,
        project_id=batch_in.project_id,
        name=batch_in.name,
        total_jobs=total_jobs,
        completed_jobs=0,
        failed_jobs=0,
        status="pending"
    )
    db.add(batch)
    
    # Helper to maps batch-local indexes/keys to created job UUIDs
    job_map: Dict[str, UUID] = {}
    
    # 2. Insert all jobs atomically
    now = datetime.now(timezone.utc)
    
    # List of job insertions and dependencies to insert
    jobs_to_create = []
    
    for idx, j_data in enumerate(batch_in.jobs):
        job_id = uuid.uuid4()
        ref_key = j_data.get("ref_key", str(idx))  # Let clients define ref_key for dependency mapping
        job_map[ref_key] = job_id
        
        queue_id_str = j_data.get("queue_id")
        if not queue_id_str:
            raise HTTPException(status_code=400, detail=f"Job index {idx} missing 'queue_id'")
        queue_id = UUID(queue_id_str)
        
        run_at_str = j_data.get("run_at")
        run_at = datetime.fromisoformat(run_at_str) if run_at_str else None
        
        status_val = "queued"
        if run_at and run_at > now:
            status_val = "scheduled"
            
        job = Job(
            id=job_id,
            queue_id=queue_id,
            batch_id=batch_id,
            type=j_data.get("type"),
            payload=j_data.get("payload", {}),
            priority=j_data.get("priority", 0),
            status=status_val,
            run_at=run_at,
            retry_policy_id=UUID(j_data["retry_policy_id"]) if j_data.get("retry_policy_id") else None,
            max_attempts=j_data.get("max_attempts", 3),
            idempotency_key=j_data.get("idempotency_key")
        )
        db.add(job)
        jobs_to_create.append((job, j_data.get("depends_on_refs", [])))
        
    # 3. Create intra-batch dependencies
    for job, dep_refs in jobs_to_create:
        for ref in dep_refs:
            if ref not in job_map:
                raise HTTPException(status_code=400, detail=f"Dependency reference '{ref}' not found in batch list")
            db.add(JobDependency(job_id=job.id, depends_on_job_id=job_map[ref]))
            
    # Promote batch status
    batch.status = "running"
    await db.commit()
    await db.refresh(batch)
    return batch

# --- Job Dependencies Creation (with cycle checking) ---

@router.post("/jobs/{job_id}/dependencies/{parent_id}", status_code=status.HTTP_201_CREATED)
async def create_job_dependency(
    job_id: UUID,
    parent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify access to the job's project
    stmt_job = select(Job).where(Job.id == job_id)
    res_job = await db.execute(stmt_job)
    job = res_job.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    await verify_queue_access(job.queue_id, current_user.id, db, roles=["owner", "admin", "member"])
    
    # Verify parent job exists
    stmt_parent = select(Job).where(Job.id == parent_id)
    res_parent = await db.execute(stmt_parent)
    if not res_parent.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Parent job not found")
        
    # Cycle check (DFS)
    if await detect_cycle(job_id, parent_id, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Circular dependency detected. Adding this dependency would create a cycle."
        )
        
    # Check if dependency already exists
    stmt_exists = select(JobDependency).where(
        JobDependency.job_id == job_id,
        JobDependency.depends_on_job_id == parent_id
    )
    res_exists = await db.execute(stmt_exists)
    if res_exists.scalar_one_or_none():
        return {"status": "success", "message": "Dependency already exists"}
        
    # Insert
    db.add(JobDependency(job_id=job_id, depends_on_job_id=parent_id))
    await db.commit()
    return {"status": "success", "message": "Dependency created successfully"}

# --- Job Inspection & Actions ---

@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await verify_queue_access(job.queue_id, current_user.id, db)
    return job

@router.get("/jobs/{job_id}/executions", response_model=List[JobExecutionOut])
async def get_job_executions(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await verify_queue_access(job.queue_id, current_user.id, db)
    
    stmt_exec = select(JobExecution).where(JobExecution.job_id == job_id).order_by(JobExecution.attempt_number.desc())
    res_exec = await db.execute(stmt_exec)
    return res_exec.scalars().all()

@router.get("/executions/{execution_id}/logs", response_model=List[JobLogOut])
async def get_execution_logs(
    execution_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Fetch execution and verify access
    stmt_exec = select(JobExecution).where(JobExecution.id == execution_id)
    res_exec = await db.execute(stmt_exec)
    execution = res_exec.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
        
    stmt_job = select(Job).where(Job.id == execution.job_id)
    res_job = await db.execute(stmt_job)
    job = res_job.scalar_one_or_none()
    if job:
        await verify_queue_access(job.queue_id, current_user.id, db)
        
    stmt_logs = select(JobLog).where(JobLog.execution_id == execution_id).order_by(JobLog.timestamp.asc())
    res_logs = await db.execute(stmt_logs)
    return res_logs.scalars().all()

@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def manual_retry_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Job).where(Job.id == job_id)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    await verify_queue_access(job.queue_id, current_user.id, db, roles=["owner", "admin"])
    
    if job.status not in ["failed", "dead_letter"]:
        raise HTTPException(status_code=400, detail="Only failed or dead_letter jobs can be manually retried.")
        
    # Reset job for retry
    job.status = "queued"
    job.attempt_count = 0  # Reset counter
    job.run_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    
    # If it was part of a batch, adjust counters or status if needed, but we keep it simple
    await db.commit()
    await db.refresh(job)
    return job

@router.get("/batches/{batch_id}", response_model=JobBatchOut)
async def get_batch(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(JobBatch).where(JobBatch.id == batch_id)
    res = await db.execute(stmt)
    batch = res.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
        
    await verify_project_access(batch.project_id, current_user.id, db)
    return batch

@router.get("/batches/{batch_id}/jobs", response_model=List[JobOut])
async def get_batch_jobs(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt_batch = select(JobBatch).where(JobBatch.id == batch_id)
    res_b = await db.execute(stmt_batch)
    batch = res_b.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
        
    await verify_project_access(batch.project_id, current_user.id, db)
    
    stmt_jobs = select(Job).where(Job.batch_id == batch_id).order_by(Job.created_at.asc())
    res_jobs = await db.execute(stmt_jobs)
    return res_jobs.scalars().all()

@router.get("/projects/{project_id}/jobs", response_model=List[JobOut])
async def list_project_jobs(
    project_id: UUID,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    await verify_project_access(project_id, current_user.id, db)
    
    stmt = select(Job).join(Queue).where(Queue.project_id == project_id, Queue.deleted_at == None)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
        
    stmt = stmt.order_by(Job.created_at.desc()).offset(offset).limit(limit)
    res = await db.execute(stmt)
    return res.scalars().all()
