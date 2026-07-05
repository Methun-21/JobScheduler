from datetime import datetime, timezone, timedelta
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from backend.app.database import get_db
from backend.app.models import Worker, User
from backend.app.schemas import WorkerOut
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/workers", tags=["workers"])

@router.get("", response_model=List[WorkerOut])
async def list_workers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Retrieve all workers sorted by registered time
    stmt = select(Worker).order_by(Worker.registered_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.delete("/offline", status_code=status.HTTP_200_OK)
async def clear_offline_workers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Workers are considered offline if heartbeat is missing for > 30 seconds
    threshold = datetime.now(timezone.utc) - timedelta(seconds=30)
    
    # We delete workers whose last_heartbeat_at is older than 30s
    stmt = delete(Worker).where(Worker.last_heartbeat_at < threshold)
    await db.execute(stmt)
    await db.commit()
    return {"status": "success", "message": "Offline workers cleaned up successfully."}
