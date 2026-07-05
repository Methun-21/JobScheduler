from datetime import timedelta
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert
from backend.app.database import get_db
from backend.app.models import User, Organization, OrgMember
from backend.app.schemas import UserCreate, UserLogin, Token, UserOut, OrgCreate, OrgOut, OrgMemberOut
from backend.app.security import hash_password, verify_password, create_access_token, decode_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login-form")

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    sub = decode_access_token(token)
    if sub is None:
        raise credentials_exception
    try:
        user_uuid = UUID(sub)
    except ValueError:
        raise credentials_exception
        
    stmt = select(User).where(User.id == user_uuid)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # Check if exists
    stmt = select(User).where(User.email == user_in.email)
    res = await db.execute(stmt)
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
        
    pwd_hash = hash_password(user_in.password)
    user = User(email=user_in.email, password_hash=pwd_hash)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.post("/login", response_model=Token)
async def login(user_in: UserLogin, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.email == user_in.email)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
        
    access_token = create_access_token(subject=user.id)
    return {"access_token": access_token, "token_type": "bearer"}

from fastapi.security import OAuth2PasswordRequestForm
@router.post("/login-form", response_model=Token)
async def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # OAuth2 password flow support
    stmt = select(User).where(User.email == form_data.username)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
        
    access_token = create_access_token(subject=user.id)
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user

# --- Organizations Management ---

@router.post("/organizations", response_model=OrgOut)
async def create_organization(
    org_in: OrgCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    org = Organization(name=org_in.name)
    db.add(org)
    # Flush to get org.id
    await db.flush()
    
    # Add creator as owner
    member = OrgMember(org_id=org.id, user_id=current_user.id, role="owner")
    db.add(member)
    await db.commit()
    await db.refresh(org)
    return org

@router.get("/organizations", response_model=List[OrgOut])
async def list_user_organizations(
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    stmt = (
        select(Organization)
        .join(OrgMember, OrgMember.org_id == Organization.id)
        .where(OrgMember.user_id == current_user.id)
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.post("/organizations/{org_id}/members", status_code=status.HTTP_201_CREATED)
async def add_organization_member(
    org_id: UUID,
    user_email: str,
    role: str = "member",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify current user is admin/owner
    stmt_role = select(OrgMember.role).where(OrgMember.org_id == org_id, OrgMember.user_id == current_user.id)
    res_role = await db.execute(stmt_role)
    caller_role = res_role.scalar_one_or_none()
    
    if not caller_role or caller_role not in ["owner", "admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owners or admins can add members.")
        
    # Get user to add
    stmt_user = select(User).where(User.email == user_email)
    res_user = await db.execute(stmt_user)
    target_user = res_user.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User with specified email not found")
        
    # Check if already a member
    stmt_member = select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == target_user.id)
    res_mem = await db.execute(stmt_member)
    if res_mem.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User is already a member of this organization")
        
    member = OrgMember(org_id=org_id, user_id=target_user.id, role=role)
    db.add(member)
    await db.commit()
    return {"status": "success", "message": "User added to organization"}

@router.get("/organizations/{org_id}/members", response_model=List[OrgMemberOut])
async def list_organization_members(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Check if Caller is member
    stmt_caller = select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == current_user.id)
    res_caller = await db.execute(stmt_caller)
    if not res_caller.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this organization's members.")
        
    stmt_members = select(OrgMember).where(OrgMember.org_id == org_id)
    res = await db.execute(stmt_members)
    return res.scalars().all()
