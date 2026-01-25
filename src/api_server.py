"""
FastAPI server for secure code execution.
"""
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from src.worker import CodeExecutor
from src.job_types import JobTypeRegistry
import time
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize job type registry
registry = JobTypeRegistry()

app = FastAPI(
    title="Secure Code Executor API",
    description="Execute AI-generated Python code in isolated containers",
    version="1.0.0"
)

# Initialize executor with registry
executor = CodeExecutor(registry=registry)


# Request/Response Models
class ExecuteRequest(BaseModel):
    """Request model for code execution."""
    code: str = Field(..., max_length=100_000, description="Python code to execute")
    input_data: dict = Field(default_factory=dict, description="Input data as JSON")
    timeout: Optional[int] = Field(default=None, ge=1, le=300, description="Timeout in seconds (uses job type default if not specified)")
    job_type: Optional[str] = Field(default=None, description="Job type name (uses 'default' if not specified)")

    @field_validator('code')
    @classmethod
    def code_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Code cannot be empty')
        return v


class ExecuteResponse(BaseModel):
    """Response model for code execution."""
    success: bool
    output: Optional[dict] = None
    execution_time: float
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: Optional[str] = None
    job_type: Optional[str] = None


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
    version: str


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
        Health status including Docker availability
    """
    # Check if Docker is available
    docker_available = False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5
        )
        docker_available = result.returncode == 0
    except Exception:
        pass
    
    return {
        "status": "healthy" if docker_available else "degraded",
        "docker_available": docker_available,
        "version": "1.0.0"
    }


@app.post("/execute", response_model=ExecuteResponse)
async def execute_code(request: ExecuteRequest):
    """
    Execute Python code in an isolated container.
    
    The code runs with:
    - No network access
    - Read-only filesystem (except /output and /tmp)
    - Limited resources (512MB RAM, 1 CPU)
    - Configurable timeout
    
    Input data is provided via /input/data.json
    Output should be written to /output/result.json
    
    Args:
        request: Execution request with code, input_data, and timeout
    
    Returns:
        Execution results including output, stdout, stderr
    
    Raises:
        HTTPException: If execution fails with 500 error
    """
    job_id = f"api-{int(time.time() * 1000)}"
    
    logger.info(f"Executing job {job_id}")
    start_time = time.time()
    
    try:
        # Create job
        job = {
            "id": job_id,
            "code": request.code,
            "input_data": request.input_data,
            "job_type": request.job_type,
        }

        # Only include timeout if explicitly specified
        if request.timeout is not None:
            job["timeout"] = request.timeout

        # Execute
        result = executor.execute_job(job)
        execution_time = time.time() - start_time

        logger.info(f"Job {job_id} completed in {execution_time:.2f}s: {'success' if result['success'] else 'failed'}")

        # Format response
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


# Job Type Management Endpoints
@app.get("/job-types", response_model=List[JobTypeResponse])
async def list_job_types():
    """
    List all registered job types.

    Returns:
        List of job type configurations with image availability status
    """
    from src.container_builder import ContainerBuilder
    builder = ContainerBuilder()

    result = []
    for jt in registry.list():
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

    Raises:
        HTTPException: If job type not found
    """
    from src.container_builder import ContainerBuilder

    jt = registry.get(name)
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
