"""
FastAPI server for secure code execution.
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, validator
from worker import CodeExecutor
import time
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Secure Code Executor API",
    description="Execute AI-generated Python code in isolated containers",
    version="1.0.0"
)

# Initialize executor
executor = CodeExecutor()


# Request/Response Models
class ExecuteRequest(BaseModel):
    """Request model for code execution."""
    code: str = Field(..., max_length=100_000, description="Python code to execute")
    input_data: dict = Field(default_factory=dict, description="Input data as JSON")
    timeout: int = Field(default=30, ge=1, le=300, description="Timeout in seconds")
    
    @validator('code')
    def code_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Code cannot be empty')
        return v


class ExecuteResponse(BaseModel):
    """Response model for code execution."""
    success: bool
    output: dict = None
    execution_time: float
    stdout: str = ""
    stderr: str = ""
    exit_code: int = None
    error: str = None


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
            "timeout": request.timeout
        }
        
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
            error=result.get("error")
        )
        
    except Exception as e:
        logger.error(f"Job {job_id} error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")


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
