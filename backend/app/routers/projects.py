from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from backend.app.database import get_db
from backend.app.models import User, Project, OrgMember
from backend.app.schemas import ProjectCreate, ProjectOut
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])

@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify current user is a member of the organization
    stmt_member = select(OrgMember).where(
        OrgMember.org_id == project_in.org_id,
        OrgMember.user_id == current_user.id
    )
    res_mem = await db.execute(stmt_member)
    if not res_mem.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to create projects in this organization."
        )
        
    project = Project(
        org_id=project_in.org_id,
        name=project_in.name,
        created_by=current_user.id
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

@router.get("/org/{org_id}", response_model=List[ProjectOut])
async def list_organization_projects(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Check org membership
    stmt_member = select(OrgMember).where(
        OrgMember.org_id == org_id,
        OrgMember.user_id == current_user.id
    )
    res_mem = await db.execute(stmt_member)
    if not res_mem.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to projects in this organization."
        )
        
    # Exclude soft-deleted projects
    stmt = select(Project).where(
        Project.org_id == org_id,
        Project.deleted_at == None
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Fetch project
    stmt = select(Project).where(Project.id == project_id, Project.deleted_at == None)
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Check org role
    stmt_member = select(OrgMember).where(
        OrgMember.org_id == project.org_id,
        OrgMember.user_id == current_user.id
    )
    res_mem = await db.execute(stmt_member)
    member = res_mem.scalar_one_or_none()
    if not member or member.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners or admins can delete projects."
        )
        
    # Soft delete project
    project.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "success", "message": "Project soft-deleted successfully"}
