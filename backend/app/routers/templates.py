from datetime import datetime, timezone
from typing import List
from uuid import UUID
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert
from croniter import croniter
from backend.app.database import get_db
from backend.app.models import User, Queue, JobTemplate, Job
from backend.app.schemas import JobTemplateCreate, JobTemplateOut
from backend.app.routers.auth import get_current_user
from backend.app.routers.jobs import verify_queue_access

router = APIRouter(tags=["templates"])

@router.post("/templates", response_model=JobTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    temp_in: JobTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    queue = await verify_queue_access(temp_in.queue_id, current_user.id, db, roles=["owner", "admin"])
    
    # Validate cron expression
    if not croniter.is_valid(temp_in.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")
        
    now = datetime.now(timezone.utc)
    
    # Calculate next execution run_at
    try:
        cron = croniter(temp_in.cron_expression, now)
        next_run = cron.get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to calculate next execution: {str(e)}")
        
    template = JobTemplate(
        queue_id=temp_in.queue_id,
        name=temp_in.name,
        type=temp_in.type,
        payload=temp_in.payload,
        cron_expression=temp_in.cron_expression,
        status="active",
        next_run_at=next_run
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template

@router.get("/queues/{queue_id}/templates", response_model=List[JobTemplateOut])
async def list_queue_templates(
    queue_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    await verify_queue_access(queue_id, current_user.id, db)
    
    stmt = select(JobTemplate).where(JobTemplate.queue_id == queue_id).order_by(JobTemplate.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.put("/templates/{template_id}/status", response_model=JobTemplateOut)
async def update_template_status(
    template_id: UUID,
    status_val: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if status_val not in ["active", "paused"]:
        raise HTTPException(status_code=400, detail="Status must be 'active' or 'paused'")
        
    stmt = select(JobTemplate).where(JobTemplate.id == template_id)
    res = await db.execute(stmt)
    template = res.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await verify_queue_access(template.queue_id, current_user.id, db, roles=["owner", "admin"])
    
    template.status = status_val
    template.updated_at = datetime.now(timezone.utc)
    
    # Recalculate next_run_at if activating
    if status_val == "active":
        cron = croniter(template.cron_expression, datetime.now(timezone.utc))
        next_run = cron.get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        template.next_run_at = next_run
        
    await db.commit()
    await db.refresh(template)
    return template

@router.post("/templates/{template_id}/trigger", response_model=JobTemplateOut)
async def trigger_template_manually(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(JobTemplate).where(JobTemplate.id == template_id)
    res = await db.execute(stmt)
    template = res.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await verify_queue_access(template.queue_id, current_user.id, db, roles=["owner", "admin", "member"])
    
    # Trigger an immediate run of the template by cloning it as a job row
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    
    await db.execute(
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
    await db.commit()
    return template

@router.delete("/templates/{template_id}", status_code=status.HTTP_200_OK)
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(JobTemplate).where(JobTemplate.id == template_id)
    res = await db.execute(stmt)
    template = res.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await verify_queue_access(template.queue_id, current_user.id, db, roles=["owner", "admin"])
    
    await db.delete(template)
    await db.commit()
    return {"status": "success", "message": "Template deleted successfully"}
