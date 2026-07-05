from datetime import datetime
from typing import Dict, Any, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, EmailStr

# Base config
class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

# --- Auth & Users ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserOut(BaseSchema):
    id: UUID
    email: EmailStr
    created_at: datetime

class OrgCreate(BaseModel):
    name: str = Field(min_length=1)

class OrgOut(BaseSchema):
    id: UUID
    name: str
    created_at: datetime

class OrgMemberOut(BaseSchema):
    org_id: UUID
    user_id: UUID
    role: str
    user: UserOut

# --- Projects ---
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    org_id: UUID

class ProjectOut(BaseSchema):
    id: UUID
    org_id: UUID
    name: str
    created_by: Optional[UUID] = None
    created_at: datetime

# --- Retry Policies ---
class RetryPolicyCreate(BaseModel):
    name: str = Field(min_length=1)
    strategy: str = Field(default="fixed", pattern="^(fixed|linear|exponential)$")
    base_delay_seconds: int = Field(default=5, gt=0)
    max_retries: int = Field(default=3, ge=0)
    max_delay_seconds: Optional[int] = Field(default=None, gt=0)

class RetryPolicyOut(BaseSchema):
    id: UUID
    name: str
    strategy: str
    base_delay_seconds: int
    max_retries: int
    max_delay_seconds: Optional[int] = None
    created_at: datetime

# --- Queues ---
class QueueCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1)
    priority: int = Field(default=1, ge=0)
    max_concurrency: int = Field(default=5, gt=0)
    default_retry_policy_id: Optional[UUID] = None

class QueueUpdate(BaseModel):
    priority: Optional[int] = Field(default=None, ge=0)
    max_concurrency: Optional[int] = Field(default=None, gt=0)
    is_paused: Optional[bool] = None
    default_retry_policy_id: Optional[UUID] = None

class QueueOut(BaseSchema):
    id: UUID
    project_id: UUID
    name: str
    priority: int
    max_concurrency: int
    is_paused: bool
    default_retry_policy_id: Optional[UUID] = None
    created_at: datetime

# --- Job Batches ---
class JobBatchCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1)
    jobs: List[Dict[str, Any]] = Field(default_factory=list)  # List of jobs to submit in batch

class JobBatchOut(BaseSchema):
    id: UUID
    project_id: UUID
    name: str
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    status: str
    created_at: datetime

# --- Job Templates ---
class JobTemplateCreate(BaseModel):
    queue_id: UUID
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    cron_expression: str = Field(min_length=1)

class JobTemplateOut(BaseSchema):
    id: UUID
    queue_id: UUID
    name: str
    type: str
    payload: Dict[str, Any]
    cron_expression: str
    status: str
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime

# --- Jobs ---
class JobCreate(BaseModel):
    queue_id: UUID
    type: str = Field(min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0)
    run_at: Optional[datetime] = None  # None = run immediately
    retry_policy_id: Optional[UUID] = None
    max_attempts: int = Field(default=3, gt=0)
    idempotency_key: Optional[str] = None
    dependencies: Optional[List[UUID]] = None  # List of job_ids this job depends on

class JobOut(BaseSchema):
    id: UUID
    queue_id: UUID
    batch_id: Optional[UUID] = None
    parent_template_id: Optional[UUID] = None
    type: str
    payload: Dict[str, Any]
    status: str
    priority: int
    run_at: Optional[datetime] = None
    retry_policy_id: Optional[UUID] = None
    attempt_count: int
    max_attempts: int
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class JobDependencyCreate(BaseModel):
    job_id: UUID
    depends_on_job_id: UUID

class JobDependencyOut(BaseSchema):
    job_id: UUID
    depends_on_job_id: UUID

# --- Job Executions & Logs ---
class JobExecutionOut(BaseSchema):
    id: UUID
    job_id: Optional[UUID] = None
    worker_id: Optional[str] = None
    attempt_number: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

class JobLogOut(BaseSchema):
    id: UUID
    execution_id: UUID
    timestamp: datetime
    level: str
    message: str

class WorkerOut(BaseSchema):
    id: str
    hostname: str
    status: str
    last_heartbeat_at: datetime
    registered_at: datetime
    current_job_id: Optional[UUID] = None

# --- Dashboards stats ---
class SystemStatsOut(BaseModel):
    total_jobs: int
    running_jobs: int
    queued_jobs: int
    completed_jobs: int
    failed_jobs: int
    dead_letter_jobs: int
    active_workers: int
    total_queues: int
    throughput_per_min: float
