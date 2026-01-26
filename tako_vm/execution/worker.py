"""
Core worker module for executing code in isolated Docker containers.

Provides both legacy dict-based results and new ExecutionRecord-based results.
"""

import os
import json
import subprocess
import tempfile
import shutil
import hashlib
from pathlib import Path
import time
import logging
from typing import Optional, List
from datetime import datetime

from tako_vm.job_types import JobType, JobTypeRegistry
from tako_vm.models import ExecutionRecord, ResourceUsage, Artifact, ExecutionError
from tako_vm.security import (
    cap_output, sanitize_error, classify_error, compute_file_hash,
    DEFAULT_MAX_STDOUT_BYTES, DEFAULT_MAX_STDERR_BYTES, DEFAULT_MAX_ARTIFACT_BYTES
)
from tako_vm.config import get_config, TakoVMConfig

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
        registry: Optional[JobTypeRegistry] = None,
        config: Optional[TakoVMConfig] = None
    ):
        """
        Initialize the executor.

        Args:
            docker_image: Default Docker image to use (for backward compatibility)
            default_timeout: Default timeout in seconds for executions
            registry: Job type registry for looking up job types
            config: Configuration (uses global config if not provided)
        """
        self.docker_image = docker_image
        self.default_timeout = default_timeout
        self.registry = registry or JobTypeRegistry()
        self.config = config or get_config()

    def _auto_build_image(self, job_type: JobType) -> bool:
        """
        Automatically build a container image for a job type.

        Args:
            job_type: Job type configuration

        Returns:
            True if build succeeded, False otherwise
        """
        # In production mode, don't auto-build
        if self.config.production_mode:
            logger.warning(f"Production mode: auto-build disabled for {job_type.name}")
            return False

        try:
            from tako_vm.container_builder import ContainerBuilder
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

        # Handle version specifier (job_type@version)
        name = job_type_name
        if '@' in job_type_name:
            name = job_type_name.split('@')[0]

        job_type = self.registry.get(name)
        if job_type is None:
            if self.config.production_mode:
                raise ValueError(f"Job type '{name}' not found (production mode)")
            logger.warning(f"Job type '{name}' not found, using default")
            return DEFAULT_JOB_TYPE

        return job_type

    def execute_job(self, job: dict) -> dict:
        """
        Execute a job in an isolated container (legacy interface).

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
        job_id = job.get("id", str(int(time.time() * 1000)))

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
            output_dir.chmod(0o777)  # Writable by container user (uid 1000)

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

    def execute_job_with_record(
        self,
        job_id: str,
        job: dict,
        api_key_id: Optional[str] = None,
        client_ip: Optional[str] = None
    ) -> ExecutionRecord:
        """
        Execute a job and return an ExecutionRecord.

        This is the new production interface that provides audit-grade records.

        Args:
            job_id: Unique job identifier
            job: Dictionary with code, input_data, timeout, job_type
            api_key_id: API key that initiated this execution
            client_ip: Client IP address

        Returns:
            ExecutionRecord with complete audit trail
        """
        # Create initial record
        code = job.get("code", "")
        input_data = job.get("input_data", {})

        record = ExecutionRecord(
            execution_id=job_id,
            status="pending",
            job_type=job.get("job_type", "default"),
            job_version="latest",  # Will be updated if version manager is used
            created_at=datetime.utcnow(),
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            input_hash=hashlib.sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest(),
            api_key_id=api_key_id,
            client_ip=client_ip,
        )

        # Get job type configuration
        try:
            job_type = self._get_job_type(job.get("job_type"))
            record.job_type = job_type.name
        except ValueError as e:
            # Job type not found in production mode
            record.status = "error"
            record.ended_at = datetime.utcnow()
            record.error = ExecutionError(type="config_error", message=str(e))
            return record

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
            output_dir.chmod(0o777)  # Writable by container user (uid 1000)

            # Write generated code to file
            code_file = code_dir / "main.py"
            code_file.write_text(code)
            code_file.chmod(0o444)

            # Write input data
            input_file = input_dir / "data.json"
            input_file.write_text(json.dumps(input_data))
            input_file.chmod(0o444)

            # Mark as running
            record.status = "running"
            record.started_at = datetime.utcnow()

            # Execute in container
            start_time = time.time()
            timed_out = False

            try:
                result = self._run_container(
                    code_dir=code_dir,
                    input_dir=input_dir,
                    output_dir=output_dir,
                    timeout=timeout,
                    job_type=job_type
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                result = {
                    "success": False,
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                }

            end_time = time.time()
            wall_time_ms = int((end_time - start_time) * 1000)

            # Update record with results
            record.ended_at = datetime.utcnow()
            record.duration_ms = wall_time_ms
            record.exit_code = result.get("exit_code")

            # Cap and sanitize outputs
            record.stdout = cap_output(
                result.get("stdout", ""),
                self.config.max_stdout_bytes
            )
            record.stderr = cap_output(
                result.get("stderr", ""),
                self.config.max_stderr_bytes
            )

            # Resource usage
            record.resource_usage = ResourceUsage(wall_time_ms=wall_time_ms)

            # Collect artifacts from output directory
            record.artifacts = self._collect_artifacts(output_dir)

            # Read main output
            output_file = output_dir / "result.json"
            if output_file.exists():
                try:
                    record.output = json.loads(output_file.read_text())
                except json.JSONDecodeError:
                    pass

            # Determine final status
            if timed_out:
                record.status = "timeout"
                record.error = ExecutionError(
                    type="timeout",
                    message=f"Execution exceeded time limit ({timeout}s)"
                )
            elif result.get("exit_code") == 137:
                record.status = "oom"
                record.error = ExecutionError(
                    type="oom",
                    message="Execution exceeded memory limit"
                )
            elif result.get("success"):
                record.status = "success"
            else:
                record.status = "error"
                error_type, error_msg = classify_error(
                    result.get("exit_code", 1),
                    result.get("stderr", ""),
                    timed_out
                )
                record.error = ExecutionError(type=error_type, message=error_msg)

            return record

        except Exception as e:
            # Unexpected error
            record.status = "error"
            record.ended_at = datetime.utcnow()
            record.error = ExecutionError(
                type="internal_error",
                message=sanitize_error(str(e))
            )
            return record

        finally:
            # Cleanup
            shutil.rmtree(workspace, ignore_errors=True)

    def _collect_artifacts(self, output_dir: Path) -> List[Artifact]:
        """
        Collect artifacts from output directory.

        Args:
            output_dir: Path to output directory

        Returns:
            List of Artifact objects
        """
        artifacts = []
        total_size = 0

        if not output_dir.exists():
            return artifacts

        for path in output_dir.iterdir():
            if not path.is_file():
                continue

            size = path.stat().st_size

            # Check individual file size limit
            if size > self.config.max_artifact_bytes:
                logger.warning(f"Artifact {path.name} exceeds size limit, skipping")
                continue

            # Check total size limit
            if total_size + size > self.config.max_total_artifacts_bytes:
                logger.warning("Total artifact size limit reached, stopping collection")
                break

            try:
                file_hash = compute_file_hash(path)
                artifacts.append(Artifact(
                    name=path.name,
                    size_bytes=size,
                    sha256=file_hash,
                    path=f"/output/{path.name}"
                ))
                total_size += size
            except Exception as e:
                logger.warning(f"Failed to process artifact {path.name}: {e}")

        return artifacts

    def _run_container(
        self,
        code_dir: Path,
        input_dir: Path,
        output_dir: Path,
        timeout: int,
        job_type: JobType
    ) -> dict:
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
                        if self.config.production_mode:
                            raise ValueError(f"Image {image_name} not found (production mode)")
                        logger.warning(f"Failed to build {image_name}, using default")
                        image_name = self.docker_image
                else:
                    image_name = self.docker_image
        except subprocess.SubprocessError:
            if self.config.production_mode:
                raise
            image_name = self.docker_image

        cmd = [
            "docker", "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
        ]

        # Network isolation (default: no network for security)
        if job_type.network_enabled:
            if job_type.allowed_hosts:
                # Allowlist configured - check if proxy network exists
                proxy_check = subprocess.run(
                    ["docker", "network", "inspect", "tako-proxy"],
                    capture_output=True
                )
                if proxy_check.returncode == 0:
                    # Proxy network exists - use it for enforcement
                    cmd.append("--network=tako-proxy")
                    allowed_hosts_str = ",".join(job_type.allowed_hosts)
                    cmd.append(f"--env=TAKO_ALLOWED_HOSTS={allowed_hosts_str}")
                    cmd.append("--env=HTTP_PROXY=http://tako-proxy:3128")
                    cmd.append("--env=HTTPS_PROXY=http://tako-proxy:3128")
                    cmd.append("--env=NO_PROXY=localhost,127.0.0.1")
                else:
                    # Proxy not configured - warn and use bridge (no enforcement)
                    logger.warning(
                        f"Job type '{job_type.name}' has allowed_hosts configured but "
                        "tako-proxy network not found. Network access is UNRESTRICTED. "
                        "Set up egress proxy for enforcement: scripts/proxy/docker-compose.yaml"
                    )
                    cmd.append("--network=bridge")
            else:
                # No allowlist - unrestricted network access
                cmd.append("--network=bridge")
        else:
            cmd.append("--network=none")  # Complete network isolation

        # Run as non-root user inside container (uid 1000 = sandbox user)
        # This ensures code never runs as root, even inside the container
        if self.config.enable_userns:
            cmd.append("--user=1000:1000")

        # Resource limits from job type
        limits = self.config.container_limits
        cmd.extend([
            f"--memory={job_type.memory_limit}",
            f"--memory-swap={job_type.memory_limit}",
            f"--cpus={job_type.cpu_limit}",
            f"--pids-limit={limits.pids_limit}",

            # Configurable ulimits
            f"--ulimit=nofile={limits.nofile_soft}:{limits.nofile_hard}",
            f"--ulimit=nproc={limits.nproc_soft}:{limits.nproc_hard}",
            f"--ulimit=fsize={limits.fsize}",

            # Mounts
            f"--mount=type=bind,source={code_dir.absolute()},target=/code,readonly",
            f"--mount=type=bind,source={input_dir.absolute()},target=/input,readonly",
            f"--mount=type=bind,source={output_dir.absolute()},target=/output",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={limits.tmpfs_size}",
        ])

        # Add seccomp profile if enabled and exists
        if self.config.enable_seccomp and self.config.seccomp_profile_path:
            if self.config.seccomp_profile_path.exists():
                cmd.append(f"--security-opt=seccomp={self.config.seccomp_profile_path}")

        # Add environment variables from job type
        for key, value in job_type.environment.items():
            cmd.append(f"--env={key}={value}")

        # Add image name
        cmd.append(image_name)

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
                "exit_code": -1,
                "timeout": timeout
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "exit_code": -1
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
