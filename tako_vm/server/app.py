"""
FastAPI server for secure code execution.

Provides both legacy synchronous execution and new async job queue execution.
"""

# Suppress LibreSSL warnings on macOS before any other imports
import warnings
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass

from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
import time
import logging
import subprocess
import uuid

from tako_vm.execution.worker import CodeExecutor
from tako_vm.execution.health import get_circuit_breaker, startup_cleanup
from tako_vm.job_types import JobTypeRegistry
from tako_vm.models import ExecutionRecord
from tako_vm.config import get_config, TakoVMConfig
from tako_vm.storage import ExecutionStorage
from tako_vm.server.queue import WorkerPool
from tako_vm.server.correlation import (
    CorrelationIdMiddleware,
    configure_logging_with_correlation,
    get_correlation_id
)

# Configure logging with correlation ID support
logging.basicConfig(level=logging.INFO)
configure_logging_with_correlation()
logger = logging.getLogger(__name__)


# Application state (initialized in lifespan)
class AppState:
    config: TakoVMConfig
    registry: JobTypeRegistry
    executor: CodeExecutor
    storage: ExecutionStorage
    worker_pool: WorkerPool


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting Tako VM API server...")

    # Run startup cleanup (orphaned containers, health check)
    cleanup_results = startup_cleanup()
    logger.info(f"Startup cleanup: {cleanup_results}")

    state.config = get_config()
    state.registry = JobTypeRegistry()
    state.executor = CodeExecutor(registry=state.registry, config=state.config)
    state.storage = ExecutionStorage(state.config.database_file)
    state.storage.init()
    state.worker_pool = WorkerPool(
        executor=state.executor,
        storage=state.storage,
        max_workers=state.config.max_workers,
        max_queue_size=state.config.max_queue_size
    )
    await state.worker_pool.start()

    logger.info(f"Tako VM ready (production_mode={state.config.production_mode})")

    yield

    # Shutdown
    logger.info("Shutting down Tako VM API server...")
    await state.worker_pool.stop()
    state.storage.close()


app = FastAPI(
    title="Tako VM - Secure Code Executor API",
    description="Execute AI-generated Python code in isolated containers",
    version="2.0.0",
    lifespan=lifespan
)

# Add correlation ID middleware (must be added before other middleware)
app.add_middleware(CorrelationIdMiddleware)


# Request/Response Models
class ExecuteRequest(BaseModel):
    """Request model for code execution."""
    code: str = Field(..., max_length=100_000, description="Python code to execute")
    input_data: dict = Field(default_factory=dict, description="Input data as JSON")
    timeout: Optional[int] = Field(default=None, ge=1, le=300, description="Timeout in seconds")
    job_type: Optional[str] = Field(default=None, description="Job type name (e.g., 'data-processing@v1.0.0')")

    @field_validator('code')
    @classmethod
    def code_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Code cannot be empty')
        return v


class ExecuteResponse(BaseModel):
    """Response model for code execution (legacy)."""
    success: bool
    output: Optional[dict] = None
    execution_time: float
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: Optional[str] = None
    job_type: Optional[str] = None


class AsyncExecuteResponse(BaseModel):
    """Response model for async execution."""
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    """Response model for job status."""
    job_id: str
    status: str
    created_at: Optional[str] = None
    duration_ms: Optional[int] = None
    queue_position: Optional[int] = None


class ExecutionRecordResponse(BaseModel):
    """Response model for execution record."""
    execution_id: str
    status: str
    job_type: str
    job_version: str
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_ms: Optional[int] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    output: Optional[dict] = None
    error: Optional[dict] = None

    @classmethod
    def from_record(cls, record: ExecutionRecord) -> 'ExecutionRecordResponse':
        return cls(
            execution_id=record.execution_id,
            status=record.status,
            job_type=record.job_type,
            job_version=record.job_version,
            created_at=record.created_at.isoformat(),
            started_at=record.started_at.isoformat() if record.started_at else None,
            ended_at=record.ended_at.isoformat() if record.ended_at else None,
            duration_ms=record.duration_ms,
            exit_code=record.exit_code,
            stdout=record.stdout,
            stderr=record.stderr,
            output=record.output,
            error=record.error.model_dump() if record.error else None,
        )


class JobTypeResponse(BaseModel):
    """Response model for job type info."""
    name: str
    requirements: List[str]
    python_version: str
    memory_limit: str
    cpu_limit: float
    timeout: int
    image_exists: bool


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    docker_available: bool
    circuit_breaker: dict
    version: str
    production_mode: bool
    queue_stats: dict


class PoolStatsResponse(BaseModel):
    """Response model for worker pool stats."""
    pending: int
    running: int
    max_workers: int
    max_queue_size: int


# Middleware for security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# Routes
@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns:
        Health status including Docker availability, circuit breaker, and queue stats
    """
    circuit_breaker = get_circuit_breaker()
    docker_available = circuit_breaker.check_docker_health()
    cb_status = circuit_breaker.get_status()

    # Determine overall status
    if not docker_available:
        status = "degraded"
    elif cb_status["state"] == "open":
        status = "degraded"
    else:
        status = "healthy"

    return HealthResponse(
        status=status,
        docker_available=docker_available,
        circuit_breaker=cb_status,
        version="2.0.0",
        production_mode=state.config.production_mode,
        queue_stats=state.worker_pool.stats
    )


@app.post("/execute", response_model=ExecuteResponse)
async def execute_code(request: ExecuteRequest):
    """
    Execute Python code synchronously (legacy endpoint).

    The code runs in an isolated container with:
    - No network access
    - Read-only filesystem (except /output and /tmp)
    - Configurable resource limits
    - Configurable timeout

    Args:
        request: Execution request with code, input_data, and timeout

    Returns:
        Execution results including output, stdout, stderr
    """
    job_id = f"api-{int(time.time() * 1000)}"

    logger.info(f"Executing job {job_id}")
    start_time = time.time()

    try:
        job = {
            "id": job_id,
            "code": request.code,
            "input_data": request.input_data,
            "job_type": request.job_type,
        }

        if request.timeout is not None:
            job["timeout"] = request.timeout

        result = state.executor.execute_job(job)
        execution_time = time.time() - start_time

        logger.info(f"Job {job_id} completed in {execution_time:.2f}s")

        return ExecuteResponse(
            success=result["success"],
            output=result.get("output"),
            execution_time=execution_time,
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=result.get("exit_code"),
            error=result.get("error"),
            job_type=result.get("job_type")
        )

    except Exception as e:
        logger.error(f"Job {job_id} error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")


@app.post("/execute/async", response_model=AsyncExecuteResponse)
async def execute_code_async(request: ExecuteRequest, http_request: Request):
    """
    Submit code for async execution.

    Returns immediately with a job_id that can be used to poll for results.

    Args:
        request: Execution request with code, input_data, and timeout

    Returns:
        Job ID and queued status
    """
    try:
        # Include correlation ID in job data for tracing
        correlation_id = get_correlation_id()

        job_data = {
            "code": request.code,
            "input_data": request.input_data,
            "job_type": request.job_type,
            "correlation_id": correlation_id,
        }

        if request.timeout is not None:
            job_data["timeout"] = request.timeout

        job_id = await state.worker_pool.submit(
            job_data=job_data,
            client_ip=http_request.client.host if http_request.client else None
        )

        logger.info(f"Job {job_id} queued (correlation_id={correlation_id})")

        return AsyncExecuteResponse(job_id=job_id, status="queued")

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get status of an async job.

    Args:
        job_id: Job ID returned from /execute/async

    Returns:
        Job status and metadata
    """
    status = state.worker_pool.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(**status)


@app.get("/jobs/{job_id}/result", response_model=ExecutionRecordResponse)
async def get_job_result(
    job_id: str,
    wait: bool = False,
    timeout: float = 30.0
):
    """
    Get result of an async job.

    Args:
        job_id: Job ID
        wait: If true, wait for job completion
        timeout: Max time to wait if wait=true

    Returns:
        Full execution record
    """
    try:
        if wait:
            record = await state.worker_pool.wait_for_result(job_id, timeout=timeout)
        else:
            record = state.storage.get_record(job_id)
            if not record:
                # Check if still in queue
                status = state.worker_pool.get_job_status(job_id)
                if status and status.get('status') in ('pending', 'running'):
                    raise HTTPException(
                        status_code=202,
                        detail="Job still in progress"
                    )
                raise HTTPException(status_code=404, detail="Job not found")

        return ExecutionRecordResponse.from_record(record)

    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Timeout waiting for job")


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """
    Cancel a queued or running job.

    Args:
        job_id: Job ID to cancel

    Returns:
        Cancellation status
    """
    success = await state.worker_pool.cancel(job_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Job not found or already completed"
        )

    return {"status": "cancelled", "job_id": job_id}


@app.get("/executions", response_model=List[ExecutionRecordResponse])
async def list_executions(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """
    List execution records.

    Args:
        status: Filter by status
        job_type: Filter by job type
        limit: Max records to return
        offset: Pagination offset

    Returns:
        List of execution records
    """
    records = state.storage.list_records(
        status=status,
        job_type=job_type,
        limit=min(limit, 1000),
        offset=offset
    )

    return [ExecutionRecordResponse.from_record(r) for r in records]


@app.get("/executions/{execution_id}", response_model=ExecutionRecordResponse)
async def get_execution(execution_id: str):
    """
    Get a specific execution record.

    Args:
        execution_id: Execution ID

    Returns:
        Full execution record
    """
    record = state.storage.get_record(execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="Execution not found")

    return ExecutionRecordResponse.from_record(record)


# Job Type Management Endpoints
@app.get("/job-types", response_model=List[JobTypeResponse])
async def list_job_types():
    """
    List all registered job types.

    Returns:
        List of job type configurations with image availability status
    """
    from tako_vm.execution.builder import ContainerBuilder
    builder = ContainerBuilder()

    result = []
    for jt in state.registry.list():
        result.append(JobTypeResponse(
            name=jt.name,
            requirements=jt.requirements,
            python_version=jt.python_version,
            memory_limit=jt.memory_limit,
            cpu_limit=jt.cpu_limit,
            timeout=jt.timeout,
            image_exists=builder.image_exists(jt)
        ))
    return result


@app.get("/job-types/{name}", response_model=JobTypeResponse)
async def get_job_type(name: str):
    """
    Get a specific job type by name.

    Args:
        name: Job type name

    Returns:
        Job type configuration
    """
    from tako_vm.execution.builder import ContainerBuilder

    jt = state.registry.get(name)
    if not jt:
        raise HTTPException(status_code=404, detail=f"Job type '{name}' not found")

    builder = ContainerBuilder()
    return JobTypeResponse(
        name=jt.name,
        requirements=jt.requirements,
        python_version=jt.python_version,
        memory_limit=jt.memory_limit,
        cpu_limit=jt.cpu_limit,
        timeout=jt.timeout,
        image_exists=builder.image_exists(jt)
    )


@app.post("/job-types/{name}/build")
async def build_job_type(name: str, version_tag: Optional[str] = None):
    """
    Explicitly build a job type container image.

    Args:
        name: Job type name
        version_tag: Optional semantic version tag

    Returns:
        Build status and version info
    """
    from tako_vm.execution.builder import ContainerBuilder
    from tako_vm.version import VersionManager

    jt = state.registry.get(name)
    if not jt:
        raise HTTPException(status_code=404, detail=f"Job type '{name}' not found")

    try:
        builder = ContainerBuilder()
        builder.build(jt, quiet=False)

        # Register version
        version_manager = VersionManager(state.storage)
        version = version_manager.register_version(
            job_type=jt,
            image_ref=jt.image_name,
            version_tag=version_tag,
            built_by="manual"
        )

        return {
            "status": "built",
            "job_type": name,
            "version": version.full_ref,
            "digest": version.short_digest,
            "image_ref": version.image_ref
        }

    except Exception as e:
        logger.error(f"Build failed for {name}: {e}")
        raise HTTPException(status_code=500, detail=f"Build failed: {str(e)}")


@app.get("/pool/stats", response_model=PoolStatsResponse)
async def get_pool_stats():
    """Get worker pool statistics."""
    return PoolStatsResponse(**state.worker_pool.stats)


# Dead Letter Queue Endpoints
@app.get("/dlq/stats")
async def get_dlq_stats():
    """
    Get dead letter queue statistics.

    Returns:
        DLQ stats including total count and breakdown by error type
    """
    return state.storage.get_dlq_stats()


@app.get("/dlq")
async def list_dlq_entries(
    error_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """
    List dead letter queue entries.

    Args:
        error_type: Filter by error type
        limit: Max entries to return
        offset: Pagination offset

    Returns:
        List of DLQ entries
    """
    entries = state.storage.list_dlq_entries(
        error_type=error_type,
        limit=min(limit, 1000),
        offset=offset
    )
    return [entry.model_dump() for entry in entries]


@app.delete("/dlq/{entry_id}")
async def remove_dlq_entry(entry_id: int):
    """
    Remove an entry from the dead letter queue.

    Args:
        entry_id: DLQ entry ID

    Returns:
        Removal status
    """
    if state.storage.remove_from_dlq(entry_id):
        return {"status": "removed", "entry_id": entry_id}
    raise HTTPException(status_code=404, detail="DLQ entry not found")


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
