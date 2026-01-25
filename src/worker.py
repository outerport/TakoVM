"""
Core worker module for executing code in isolated Docker containers.
"""
import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
import time
import logging
from typing import Optional

from src.job_types import JobType, JobTypeRegistry

logger = logging.getLogger(__name__)


# Default job type for backward compatibility
DEFAULT_JOB_TYPE = JobType(
    name="default",
    requirements=[],
    memory_limit="512m",
    cpu_limit=1.0,
    timeout=30,
)


class CodeExecutor:
    """Execute Python code in isolated Docker containers."""

    def __init__(
        self,
        docker_image: str = "code-executor:latest",
        default_timeout: int = 30,
        registry: Optional[JobTypeRegistry] = None
    ):
        """
        Initialize the executor.

        Args:
            docker_image: Default Docker image to use (for backward compatibility)
            default_timeout: Default timeout in seconds for executions
            registry: Job type registry for looking up job types
        """
        self.docker_image = docker_image
        self.default_timeout = default_timeout
        self.registry = registry or JobTypeRegistry()
    
    def _auto_build_image(self, job_type: JobType) -> bool:
        """
        Automatically build a container image for a job type.

        Args:
            job_type: Job type configuration

        Returns:
            True if build succeeded, False otherwise
        """
        try:
            from src.container_builder import ContainerBuilder
            builder = ContainerBuilder()
            builder.build(job_type, quiet=True)
            return True
        except Exception as e:
            logger.error(f"Auto-build failed for {job_type.name}: {e}")
            return False

    def _get_job_type(self, job_type_name: Optional[str]) -> JobType:
        """
        Get job type configuration.

        Args:
            job_type_name: Name of job type, or None for default

        Returns:
            JobType configuration
        """
        if job_type_name is None:
            return DEFAULT_JOB_TYPE

        job_type = self.registry.get(job_type_name)
        if job_type is None:
            logger.warning(f"Job type '{job_type_name}' not found, using default")
            return DEFAULT_JOB_TYPE

        return job_type

    def execute_job(self, job):
        """
        Execute a job in an isolated container.

        Args:
            job: Dictionary with keys:
                - id: Job identifier (optional)
                - code: Python code to execute (string)
                - input_data: Input data as dictionary
                - timeout: Timeout in seconds (optional, uses job type default)
                - job_type: Name of job type (optional, uses "default" if not provided)

        Returns:
            Dictionary with execution results:
                - success: Boolean indicating if execution succeeded
                - output: Parsed output data from /output/result.json (if exists)
                - stdout: Standard output from execution
                - stderr: Standard error from execution
                - exit_code: Process exit code
                - error: Error message (if failed)
                - job_type: Name of job type used
        """
        job_id = job.get("id", int(time.time()))

        # Get job type configuration
        job_type = self._get_job_type(job.get("job_type"))

        # Use job-specific timeout, or job type default
        timeout = job.get("timeout", job_type.timeout)
        
        # Create temporary workspace
        workspace = Path(tempfile.mkdtemp(prefix=f"job-{job_id}-"))
        
        try:
            # Prepare directories
            code_dir = workspace / "code"
            input_dir = workspace / "input"
            output_dir = workspace / "output"
            
            code_dir.mkdir()
            input_dir.mkdir()
            output_dir.mkdir()
            
            # Write generated code to file
            code_file = code_dir / "main.py"
            code_file.write_text(job["code"])
            code_file.chmod(0o444)  # Read-only
            
            # Write input data
            input_file = input_dir / "data.json"
            input_file.write_text(json.dumps(job["input_data"]))
            input_file.chmod(0o444)  # Read-only
            
            # Execute in container
            result = self._run_container(
                code_dir=code_dir,
                input_dir=input_dir,
                output_dir=output_dir,
                timeout=timeout,
                job_type=job_type
            )

            # Add job type info to result
            result["job_type"] = job_type.name
            
            # Read output
            output_file = output_dir / "result.json"
            if output_file.exists():
                try:
                    output_data = json.loads(output_file.read_text())
                    result["output"] = output_data
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse output JSON: {e}")
                    result["output"] = None
            
            return result
            
        finally:
            # Cleanup
            shutil.rmtree(workspace, ignore_errors=True)
    
    def _run_container(
        self,
        code_dir: Path,
        input_dir: Path,
        output_dir: Path,
        timeout: int,
        job_type: JobType
    ):
        """
        Run Docker container with security restrictions.

        Args:
            code_dir: Path to directory containing code (will be mounted read-only)
            input_dir: Path to directory containing input data (will be mounted read-only)
            output_dir: Path to directory for output (will be mounted read-write)
            timeout: Timeout in seconds
            job_type: Job type configuration for container settings

        Returns:
            Dictionary with execution results
        """
        # Determine which image to use
        # If job type has a specific image built, use it; otherwise fall back to default
        image_name = job_type.image_name if job_type.name != "default" else self.docker_image

        # Check if job type image exists
        try:
            check = subprocess.run(
                ["docker", "image", "inspect", image_name],
                capture_output=True
            )
            if check.returncode != 0:
                # Image doesn't exist - try to auto-build it
                if job_type.name != "default":
                    logger.info(f"Image {image_name} not found, attempting auto-build...")
                    if self._auto_build_image(job_type):
                        logger.info(f"Successfully built {image_name}")
                    else:
                        logger.warning(f"Failed to build {image_name}, using default")
                        image_name = self.docker_image
                else:
                    image_name = self.docker_image
        except Exception:
            image_name = self.docker_image

        cmd = [
            "docker", "run",
            "--rm",                                 # Remove container after execution
            "--network=none",                       # No network access
            "--read-only",                          # Read-only root filesystem
            "--cap-drop=ALL",                       # Drop all capabilities
            "--security-opt=no-new-privileges",     # Prevent privilege escalation

            # Resource limits from job type
            f"--memory={job_type.memory_limit}",
            f"--memory-swap={job_type.memory_limit}",  # No swap
            f"--cpus={job_type.cpu_limit}",
            "--pids-limit=100",

            # Mounts (use absolute paths)
            f"--mount=type=bind,source={code_dir.absolute()},target=/code,readonly",
            f"--mount=type=bind,source={input_dir.absolute()},target=/input,readonly",
            f"--mount=type=bind,source={output_dir.absolute()},target=/output",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=100m",

            # Image
            image_name
        ]

        # Add environment variables from job type
        for key, value in job_type.environment.items():
            cmd.insert(-1, f"--env={key}={value}")
        
        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True
            )
            
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Execution timeout exceeded ({timeout}s)",
                "stdout": "",
                "stderr": "",
                "timeout": timeout
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": ""
            }


if __name__ == "__main__":
    # Example usage
    executor = CodeExecutor()
    
    job = {
        "id": "test-123",
        "code": """
import json

# Read input
with open('/input/data.json') as f:
    data = json.load(f)

# Process (example: double all numbers)
result = {k: v * 2 for k, v in data.items()}

# Write output
with open('/output/result.json', 'w') as f:
    json.dump(result, f)

print("Processing complete!")
""",
        "input_data": {"a": 1, "b": 2, "c": 3}
    }
    
    result = executor.execute_job(job)
    print(json.dumps(result, indent=2))
