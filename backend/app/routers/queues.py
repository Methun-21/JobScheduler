from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.database import get_db
from backend.app.models import User, Project, Queue, RetryPolicy, OrgMember
from backend.app.schemas import QueueCreate, QueueUpdate, QueueOut, RetryPolicyCreate, RetryPolicyOut
from backend.app.routers.auth import get_current_user

router = APIRouter(tags=["queues"])

# Helpers
async def verify_project_access(project_id: UUID, user_id: UUID, db: AsyncSession, roles=None):
    stmt_proj = select(Project).where(Project.id == project_id, Project.deleted_at == None)
    res_proj = await db.execute(stmt_proj)
    project = res_proj.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    stmt_mem = select(OrgMember).where(
        OrgMember.org_id == project.org_id,
        OrgMember.user_id == user_id
    )
    res_mem = await db.execute(stmt_mem)
    member = res_mem.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail="You do not have access to this organization.")
        
    if roles and member.role not in roles:
        raise HTTPException(status_code=403, detail=f"Action requires one of roles: {roles}")

# --- Retry Policies ---

@router.post("/retry-policies", response_model=RetryPolicyOut, status_code=status.HTTP_201_CREATED)
async def create_retry_policy(
    policy_in: RetryPolicyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Anyone authenticated can create retry policies for now
    policy = RetryPolicy(
        name=policy_in.name,
        strategy=policy_in.strategy,
        base_delay_seconds=policy_in.base_delay_seconds,
        max_retries=policy_in.max_retries,
        max_delay_seconds=policy_in.max_delay_seconds
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy

@router.get("/retry-policies", response_model=List[RetryPolicyOut])
async def list_retry_policies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(RetryPolicy).order_by(RetryPolicy.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

# --- Queues ---

@router.post("/queues", response_model=QueueOut, status_code=status.HTTP_201_CREATED)
async def create_queue(
    queue_in: QueueCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify write access (owners/admins)
    await verify_project_access(queue_in.project_id, current_user.id, db, roles=["owner", "admin"])
    
    # Check if a queue with the same name exists
    stmt_exists = select(Queue).where(
        Queue.project_id == queue_in.project_id,
        Queue.name == queue_in.name,
        Queue.deleted_at == None
    )
    res_exists = await db.execute(stmt_exists)
    if res_exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Queue name already exists in this project.")
        
    queue = Queue(
        project_id=queue_in.project_id,
        name=queue_in.name,
        priority=queue_in.priority,
        max_concurrency=queue_in.max_concurrency,
        default_retry_policy_id=queue_in.default_retry_policy_id
    )
    db.add(queue)
    await db.commit()
    await db.refresh(queue)
    return queue

@router.get("/projects/{project_id}/queues", response_model=List[QueueOut])
async def list_project_queues(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    await verify_project_access(project_id, current_user.id, db)
    
    stmt = select(Queue).where(Queue.project_id == project_id, Queue.deleted_at == None).order_by(Queue.priority.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.put("/queues/{queue_id}", response_model=QueueOut)
async def update_queue(
    queue_id: UUID,
    queue_update: QueueUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Queue).where(Queue.id == queue_id, Queue.deleted_at == None)
    res = await db.execute(stmt)
    queue = res.scalar_one_or_none()
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")
        
    # Check project access
    await verify_project_access(queue.project_id, current_user.id, db, roles=["owner", "admin"])
    
    # Update fields
    if queue_update.priority is not None:
        queue.priority = queue_update.priority
    if queue_update.max_concurrency is not None:
        queue.max_concurrency = queue_update.max_concurrency
    if queue_update.is_paused is not None:
        queue.is_paused = queue_update.is_paused
    if queue_update.default_retry_policy_id is not None:
        # Verify policy exists
        if queue_update.default_retry_policy_id:
            stmt_pol = select(RetryPolicy).where(RetryPolicy.id == queue_update.default_retry_policy_id)
            res_pol = await db.execute(stmt_pol)
            if not res_pol.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Invalid default_retry_policy_id")
        queue.default_retry_policy_id = queue_update.default_retry_policy_id
        
    await db.commit()
    await db.refresh(queue)
    return queue

@router.delete("/queues/{queue_id}", status_code=status.HTTP_200_OK)
async def delete_queue(
    queue_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(Queue).where(Queue.id == queue_id, Queue.deleted_at == None)
    res = await db.execute(stmt)
    queue = res.scalar_one_or_none()
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")
        
    await verify_project_access(queue.project_id, current_user.id, db, roles=["owner", "admin"])
    
    # Soft delete
    queue.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "success", "message": "Queue soft-deleted successfully"}
