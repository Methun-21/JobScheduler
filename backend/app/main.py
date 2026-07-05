from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.scheduler import scheduler_instance
from backend.app.routers import auth, projects, queues, jobs, templates, workers, stats

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await scheduler_instance.start()
    yield
    # Shutdown
    await scheduler_instance.stop()

app = FastAPI(
    title="Distributed Job Scheduler API",
    description="Backend API platform for managing queues, scheduling and executing distributed background jobs.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For local development ease, restrict in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(queues.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(workers.router, prefix="/api")
app.include_router(stats.router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Distributed Job Scheduler API is online. Documentation is available at /docs"}
