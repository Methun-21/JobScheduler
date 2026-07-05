import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, ForeignKey, 
    Index, UniqueConstraint, text, Uuid, Text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from backend.app.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow)
    
    # Relationships
    projects = relationship("Project", back_populates="creator")

class Organization(Base):
    __tablename__ = "organizations"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    
    # Relationships
    members = relationship("OrgMember", back_populates="organization", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="organization", cascade="all, delete-orphan")

class OrgMember(Base):
    __tablename__ = "org_members"
    
    org_id = Column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(50), nullable=False, default="member")  # owner, admin, member
    
    # Relationships
    organization = relationship("Organization", back_populates="members")
    user = relationship("User")

class Project(Base):
    __tablename__ = "projects"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id = Column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    created_by = Column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # Soft-delete
    
    # Relationships
    organization = relationship("Organization", back_populates="projects")
    creator = relationship("User", back_populates="projects")
    queues = relationship("Queue", back_populates="project", cascade="all, delete-orphan")
    batches = relationship("JobBatch", back_populates="project", cascade="all, delete-orphan")

class RetryPolicy(Base):
    __tablename__ = "retry_policies"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    strategy = Column(String(50), nullable=False, default="fixed")  # fixed, linear, exponential
    base_delay_seconds = Column(Integer, nullable=False, default=5)
    max_retries = Column(Integer, nullable=False, default=3)
    max_delay_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    
    queues = relationship("Queue", back_populates="retry_policy")
    jobs = relationship("Job", back_populates="retry_policy")

class Queue(Base):
    __tablename__ = "queues"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_queue_name"),
    )
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id = Column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=1)  # higher executes first
    max_concurrency = Column(Integer, nullable=False, default=5)
    is_paused = Column(Boolean, nullable=False, default=False)
    default_retry_policy_id = Column(Uuid, ForeignKey("retry_policies.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # Soft-delete
    
    # Relationships
    project = relationship("Project", back_populates="queues")
    retry_policy = relationship("RetryPolicy", back_populates="queues")
    jobs = relationship("Job", back_populates="queue", cascade="all, delete-orphan")
    templates = relationship("JobTemplate", back_populates="queue", cascade="all, delete-orphan")

class JobBatch(Base):
    __tablename__ = "job_batches"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id = Column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    total_jobs = Column(Integer, nullable=False, default=0)
    completed_jobs = Column(Integer, nullable=False, default=0)
    failed_jobs = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    
    # Relationships
    project = relationship("Project", back_populates="batches")
    jobs = relationship("Job", back_populates="batch")

class JobTemplate(Base):
    __tablename__ = "job_templates"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    queue_id = Column(Uuid, ForeignKey("queues.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(255), nullable=False)  # Job handler type
    payload = Column(JSONB, nullable=False, default=dict)
    cron_expression = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="active")  # active, paused
    next_run_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow)
    
    # Relationships
    queue = relationship("Queue", back_populates="templates")
    instances = relationship("Job", back_populates="parent_template")

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("queue_id", "idempotency_key", name="uq_queue_idempotency"),
        Index("idx_jobs_poll", "queue_id", "status", "priority", "run_at"),
    )
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    queue_id = Column(Uuid, ForeignKey("queues.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id = Column(Uuid, ForeignKey("job_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_template_id = Column(Uuid, ForeignKey("job_templates.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(String(255), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    status = Column(String(50), nullable=False, default="queued", index=True)  # queued, scheduled, claimed, running, completed, failed, dead_letter
    priority = Column(Integer, nullable=False, default=0)
    run_at = Column(DateTime(timezone=True), nullable=True, index=True)  # Null means immediate
    retry_policy_id = Column(Uuid, ForeignKey("retry_policies.id", ondelete="SET NULL"), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    idempotency_key = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow)
    
    # Relationships
    queue = relationship("Queue", back_populates="jobs")
    batch = relationship("JobBatch", back_populates="jobs")
    parent_template = relationship("JobTemplate", back_populates="instances")
    retry_policy = relationship("RetryPolicy", back_populates="jobs")
    executions = relationship("JobExecution", back_populates="job", cascade="save-update, merge, refresh-expire")

class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (
        Index("idx_job_deps_depends_on", "depends_on_job_id"),
    )
    
    job_id = Column(Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    depends_on_job_id = Column(Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)

class JobExecution(Base):
    __tablename__ = "job_executions"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id = Column(Uuid, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    worker_id = Column(String(255), ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False)  # running, completed, failed, timed_out
    started_at = Column(DateTime(timezone=True), server_default=text("now()"))
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    result = Column(JSONB, nullable=True)
    
    # Relationships
    job = relationship("Job", back_populates="executions")
    worker = relationship("Worker", back_populates="executions")
    logs = relationship("JobLog", back_populates="execution", cascade="all, delete-orphan")

class JobLog(Base):
    __tablename__ = "job_logs"
    
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    execution_id = Column(Uuid, ForeignKey("job_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=text("now()"))
    level = Column(String(50), nullable=False)  # info, warn, error
    message = Column(Text, nullable=False)
    
    # Relationships
    execution = relationship("JobExecution", back_populates="logs")

class Worker(Base):
    __tablename__ = "workers"
    
    id = Column(String(255), primary_key=True)
    hostname = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="idle")  # idle, busy, offline
    last_heartbeat_at = Column(DateTime(timezone=True), server_default=text("now()"))
    registered_at = Column(DateTime(timezone=True), server_default=text("now()"))
    current_job_id = Column(Uuid, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    
    # Relationships
    executions = relationship("JobExecution", back_populates="worker")
