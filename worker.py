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

logger = logging.getLogger(__name__)


class CodeExecutor:
    """Execute Python code in isolated Docker containers."""
    
    def __init__(self, docker_image="code-executor:latest", default_timeout=30):
        """
        Initialize the executor.
        
        Args:
            docker_image: Name of the Docker image to use
            default_timeout: Default timeout in seconds for executions
        """
        self.docker_image = docker_image
        self.default_timeout = default_timeout
    
    def execute_job(self, job):
        """
        Execute a job in an isolated container.
        
        Args:
            job: Dictionary with keys:
                - id: Job identifier (optional)
                - code: Python code to execute (string)
                - input_data: Input data as dictionary
                - timeout: Timeout in seconds (optional, uses default if not provided)
        
        Returns:
            Dictionary with execution results:
                - success: Boolean indicating if execution succeeded
                - output: Parsed output data from /output/result.json (if exists)
                - stdout: Standard output from execution
                - stderr: Standard error from execution
                - exit_code: Process exit code
                - error: Error message (if failed)
        """
        job_id = job.get("id", int(time.time()))
        timeout = job.get("timeout", self.default_timeout)
        
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
                timeout=timeout
            )
            
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
    
    def _run_container(self, code_dir, input_dir, output_dir, timeout):
        """
        Run Docker container with security restrictions.
        
        Args:
            code_dir: Path to directory containing code (will be mounted read-only)
            input_dir: Path to directory containing input data (will be mounted read-only)
            output_dir: Path to directory for output (will be mounted read-write)
            timeout: Timeout in seconds
        
        Returns:
            Dictionary with execution results
        """
        cmd = [
            "docker", "run",
            "--rm",                                 # Remove container after execution
            "--network=none",                       # No network access
            "--read-only",                          # Read-only root filesystem
            "--cap-drop=ALL",                       # Drop all capabilities
            "--security-opt=no-new-privileges",     # Prevent privilege escalation
            
            # Resource limits
            "--memory=512m",                        # Maximum 512MB RAM
            "--memory-swap=512m",                   # No swap (same as memory limit)
            "--cpus=1",                             # Maximum 1 CPU core
            "--pids-limit=100",                     # Maximum 100 processes
            
            # Mounts (use absolute paths)
            f"--mount=type=bind,source={code_dir.absolute()},target=/code,readonly",
            f"--mount=type=bind,source={input_dir.absolute()},target=/input,readonly",
            f"--mount=type=bind,source={output_dir.absolute()},target=/output",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=100m",  # Temporary filesystem (100MB limit)
            
            # Image
            self.docker_image
        ]
        
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
