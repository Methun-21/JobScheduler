import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from backend.app.models import User, Organization, OrgMember, Project, Queue, Job, JobDependency, RetryPolicy, JobBatch
from backend.app.worker import WorkerDaemon

@pytest.mark.asyncio
async def test_auth_and_project_flow(client: AsyncClient, db: AsyncSession):
    # 1. Register User
    res = await client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "password123"
    })
    assert res.status_code == 201
    user_data = res.json()
    assert user_data["email"] == "test@example.com"
    
    # 2. Login User
    res_login = await client.post("/api/auth/login", json={
        "email": "test@example.com",
        "password": "password123"
    })
    assert res_login.status_code == 200
    token = res_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Get Profile
    res_me = await client.get("/api/auth/me", headers=headers)
    assert res_me.status_code == 200
    
    # 3. Create Org
    res_org = await client.post("/api/auth/organizations", json={"name": "Test Org"}, headers=headers)
    assert res_org.status_code == 200
    org_id = res_org.json()["id"]
    
    # 4. Create Project
    res_proj = await client.post("/api/projects", json={
        "name": "Test Project",
        "org_id": org_id
    }, headers=headers)
    assert res_proj.status_code == 201
    proj_id = res_proj.json()["id"]
    
    # 5. Create Queue
    res_q = await client.post("/api/queues", json={
        "project_id": proj_id,
        "name": "high-priority",
        "priority": 10,
        "max_concurrency": 2
    }, headers=headers)
    assert res_q.status_code == 201
    q_id = res_q.json()["id"]

@pytest.mark.asyncio
async def test_concurrency_claiming_limit(client: AsyncClient, db: AsyncSession):
    # Setup users, project, queue
    user = User(email="test2@example.com", password_hash="hash")
    db.add(user)
    await db.flush()
    
    org = Organization(name="Test Org")
    db.add(org)
    await db.flush()
    
    member = OrgMember(org_id=org.id, user_id=user.id, role="owner")
    db.add(member)
    
    project = Project(org_id=org.id, name="Proj", created_by=user.id)
    db.add(project)
    await db.flush()
    
    queue = Queue(project_id=project.id, name="concur-q", priority=1, max_concurrency=2)
    db.add(queue)
    await db.flush()
    
    # Insert 3 jobs in this queue
    job1 = Job(id=uuid.uuid4(), queue_id=queue.id, type="email_send", payload={"to_email": "a@b.com", "subject": "hi", "body": "yo"}, status="queued", priority=1)
    job2 = Job(id=uuid.uuid4(), queue_id=queue.id, type="email_send", payload={"to_email": "a@b.com", "subject": "hi", "body": "yo"}, status="queued", priority=2)
    job3 = Job(id=uuid.uuid4(), queue_id=queue.id, type="email_send", payload={"to_email": "a@b.com", "subject": "hi", "body": "yo"}, status="queued", priority=3)
    db.add_all([job1, job2, job3])
    await db.commit()
    
    # Spin up worker daemon (with max_local_concurrency = 5)
    worker = WorkerDaemon(max_local_concurrency=5)
    worker.worker_id = "test-worker-1"
    
    # Claim job 1
    c1 = await worker._claim_job_from_queue(queue.id)
    assert c1 is not None
    # Highest priority is job3
    assert c1["id"] == job3.id
    
    # Manually mark c1 as running in DB
    await db.execute(update(Job).where(Job.id == c1["id"]).values(status="running"))
    await db.commit()
            
    # Claim job 2
    c2 = await worker._claim_job_from_queue(queue.id)
    assert c2 is not None
    assert c2["id"] == job2.id  # Next highest priority
    
    await db.execute(update(Job).where(Job.id == c2["id"]).values(status="running"))
    await db.commit()
            
    # Claim job 3: Should fail because queue max_concurrency is 2 and 2 are running
    c3 = await worker._claim_job_from_queue(queue.id)
    assert c3 is None

@pytest.mark.asyncio
async def test_dag_dependencies_claiming(client: AsyncClient, db: AsyncSession):
    # Setup
    user = User(email="test3@example.com", password_hash="hash")
    db.add(user)
    await db.flush()
    org = Organization(name="Test Org")
    db.add(org)
    await db.flush()
    project = Project(org_id=org.id, name="Proj", created_by=user.id)
    db.add(project)
    await db.flush()
    queue = Queue(project_id=project.id, name="dag-q", priority=1, max_concurrency=5)
    db.add(queue)
    await db.flush()
    
    # Insert Parent and Child jobs
    parent = Job(id=uuid.uuid4(), queue_id=queue.id, type="email_send", payload={"to_email": "a@b.com", "subject": "hi", "body": "yo"}, status="queued", priority=1)
    child = Job(id=uuid.uuid4(), queue_id=queue.id, type="email_send", payload={"to_email": "a@b.com", "subject": "hi", "body": "yo"}, status="queued", priority=1)
    db.add_all([parent, child])
    await db.flush()
    
    # Establish dependency (child depends on parent)
    dep = JobDependency(job_id=child.id, depends_on_job_id=parent.id)
    db.add(dep)
    await db.commit()
    
    worker = WorkerDaemon()
    worker.worker_id = "test-worker-2"
    
    # Claiming should select parent first because child depends on parent
    c1 = await worker._claim_job_from_queue(queue.id)
    assert c1 is not None
    assert c1["id"] == parent.id
    
    # Try claiming next job (which is child): should return None since parent is still status='claimed' (not completed)
    await db.execute(update(Job).where(Job.id == parent.id).values(status="claimed"))
    await db.commit()
            
    c2 = await worker._claim_job_from_queue(queue.id)
    assert c2 is None
    
    # Complete parent job
    await db.execute(update(Job).where(Job.id == parent.id).values(status="completed"))
    await db.commit()
            
    # Now child should be claimable!
    c3 = await worker._claim_job_from_queue(queue.id)
    assert c3 is not None
    assert c3["id"] == child.id

@pytest.mark.asyncio
async def test_cycle_detection(client: AsyncClient, db: AsyncSession):
    # Setup user
    res_user = await client.post("/api/auth/register", json={"email": "cycle@example.com", "password": "password"})
    token = (await client.post("/api/auth/login", json={"email": "cycle@example.com", "password": "password"})).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.post("/api/auth/organizations", json={"name": "Org"}, headers=headers)).json()["id"]
    proj_id = (await client.post("/api/projects", json={"name": "Proj", "org_id": org_id}, headers=headers)).json()["id"]
    q_id = (await client.post("/api/queues", json={"project_id": proj_id, "name": "Q"}, headers=headers)).json()["id"]
    
    # Create 3 jobs
    j1 = (await client.post("/api/jobs", json={"queue_id": q_id, "type": "email_send", "payload": {"to_email": "x@y.com", "subject": "a", "body": "b"}}, headers=headers)).json()["id"]
    j2 = (await client.post("/api/jobs", json={"queue_id": q_id, "type": "email_send", "payload": {"to_email": "x@y.com", "subject": "a", "body": "b"}}, headers=headers)).json()["id"]
    j3 = (await client.post("/api/jobs", json={"queue_id": q_id, "type": "email_send", "payload": {"to_email": "x@y.com", "subject": "a", "body": "b"}}, headers=headers)).json()["id"]
    
    # Create dependencies: j2 depends on j1
    res_d1 = await client.post(f"/api/jobs/{j2}/dependencies/{j1}", headers=headers)
    assert res_d1.status_code == 201
    
    # j3 depends on j2
    res_d2 = await client.post(f"/api/jobs/{j3}/dependencies/{j2}", headers=headers)
    assert res_d2.status_code == 201
    
    # Attempting to make j1 depend on j3 should fail (cycle: j1 -> j3 -> j2 -> j1)
    res_cycle = await client.post(f"/api/jobs/{j1}/dependencies/{j3}", headers=headers)
    assert res_cycle.status_code == 400
    assert "Circular dependency detected" in res_cycle.json()["detail"]

@pytest.mark.asyncio
async def test_retry_delay_calculations(client: AsyncClient, db: AsyncSession):
    # Setup retry policy
    policy = RetryPolicy(id=uuid.uuid4(), name="exp-pol", strategy="exponential", base_delay_seconds=3, max_retries=3, max_delay_seconds=10)
    db.add(policy)
    await db.commit()
    
    worker = WorkerDaemon()
    
    # Attempt 1 -> base * (2^0) = 3s
    d1 = await worker._calculate_backoff_delay(db, policy.id, 1)
    assert d1 == 3
    
    # Attempt 2 -> base * (2^1) = 6s
    d2 = await worker._calculate_backoff_delay(db, policy.id, 2)
    assert d2 == 6
    
    # Attempt 3 -> base * (2^2) = 12s, capped to max_delay 10s
    d3 = await worker._calculate_backoff_delay(db, policy.id, 3)
    assert d3 == 10
