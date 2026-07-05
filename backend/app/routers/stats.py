from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.app.database import get_db, AsyncSessionLocal
from backend.app.models import Job, Worker, Queue, JobExecution, User
from backend.app.schemas import SystemStatsOut
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])

async def calculate_system_stats(db: AsyncSession, project_id: Optional[UUID] = None) -> SystemStatsOut:
    now = datetime.now(timezone.utc)
    worker_threshold = now - timedelta(seconds=30)
    throughput_threshold = now - timedelta(minutes=1)
    
    # Base queries
    stmt_jobs = select(Job.status, func.count(Job.id)).group_by(Job.status)
    stmt_workers = select(func.count(Worker.id)).where(Worker.last_heartbeat_at >= worker_threshold)
    stmt_queues = select(func.count(Queue.id)).where(Queue.deleted_at == None)
    stmt_throughput = select(func.count(JobExecution.id)).where(
        JobExecution.status == "completed",
        JobExecution.finished_at >= throughput_threshold
    )
    
    if project_id:
        stmt_jobs = stmt_jobs.join(Queue).where(Queue.project_id == project_id)
        stmt_queues = stmt_queues.where(Queue.project_id == project_id)
        stmt_throughput = stmt_throughput.join(Job).join(Queue).where(Queue.project_id == project_id)
        
    # Execute
    res_jobs = await db.execute(stmt_jobs)
    job_counts = dict(res_jobs.all())  # e.g., {'completed': 5, 'failed': 2}
    
    res_workers = await db.execute(stmt_workers)
    active_workers = res_workers.scalar() or 0
    
    res_queues = await db.execute(stmt_queues)
    total_queues = res_queues.scalar() or 0
    
    res_tp = await db.execute(stmt_throughput)
    completed_last_min = res_tp.scalar() or 0
    
    # Map status counts
    total = sum(job_counts.values())
    
    return SystemStatsOut(
        total_jobs=total,
        running_jobs=job_counts.get("running", 0) + job_counts.get("claimed", 0),
        queued_jobs=job_counts.get("queued", 0),
        completed_jobs=job_counts.get("completed", 0),
        failed_jobs=job_counts.get("failed", 0),
        dead_letter_jobs=job_counts.get("dead_letter", 0),
        active_workers=active_workers,
        total_queues=total_queues,
        throughput_per_min=float(completed_last_min)
    )

@router.get("", response_model=SystemStatsOut)
async def get_system_stats(
    project_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return await calculate_system_stats(db, project_id)

@router.websocket("/ws")
async def stats_websocket_endpoint(websocket: WebSocket, project_id: Optional[str] = None):
    await websocket.accept()
    
    proj_uuid = None
    if project_id:
        try:
            proj_uuid = UUID(project_id)
        except ValueError:
            await websocket.close(code=4000, reason="Invalid project_id UUID")
            return
            
    try:
        while True:
            # We open a session manually for each stats loop iteration since it is async/WebSocket
            async with AsyncSessionLocal() as session:
                try:
                    stats = await calculate_system_stats(session, proj_uuid)
                    await websocket.send_json(stats.model_dump())
                except Exception as e:
                    # Log internally, don't crash connection
                    pass
            await asyncio.sleep(2.0)  # Stream stats every 2s
    except WebSocketDisconnect:
        # Client disconnected
        pass
    except Exception:
        pass
