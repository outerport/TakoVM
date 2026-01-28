"""
Core worker module for executing code in isolated Docker containers.

Provides both legacy dict-based results and new ExecutionRecord-based results.
"""

import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
import time
import logging
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone

from tako_vm.job_types import JobType, JobTypeRegistry
from tako_vm.models import (
    ExecutionRecord, ResourceUsage, Artifact, InputArtifact, ExecutionError,
    sha256_json, sha256_content
)
from tako_vm.security import (
    cap_output, sanitize_error, classify_error, compute_file_hash,
    validate_env_key, validate_env_value, is_safe_filename,
    validate_pip_requirement, validate_docker_image
)
from tako_vm.config import get_config, TakoVMConfig
from tako_vm.execution.health import get_circuit_breaker
from tako_vm.execution.retry import RetryConfig, RetryContext, is_transient_error

logger = logging.getLogger(__name__)

# Workspace directory for job files (can be set via TAKO_VM_WORKSPACE env var)
# When running the server in a container with Docker socket mounted, this must
# be a path that exists on the host and is mounted into the server container.
WORKSPACE_DIR = os.environ.get("TAKO_VM_WORKSPACE", tempfile.gettempdir())

# Maximum number of runtime requirements to prevent env var overflow and slow startups
MAX_REQUIREMENTS = 50

# Docker volume name for uv cache (speeds up repeated dependency installs)
UV_CACHE_VOLUME = "tako-uv-cache"


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

    def execute_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
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
        workspace = Path(tempfile.mkdtemp(prefix=f"job-{job_id}-", dir=WORKSPACE_DIR))

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
                job_type=job_type,
                extra_requirements=job.get("requirements"),
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
            # Cleanup workspace with error logging
            try:
                shutil.rmtree(workspace)
            except Exception as cleanup_err:
                logger.error(
                    "Failed to cleanup workspace %s: %s. "
                    "Manual cleanup may be required.",
                    workspace, cleanup_err
                )

    def execute_job_with_record(
        self,
        job_id: str,
        job: Dict[str, Any],
        client_ip: Optional[str] = None
    ) -> ExecutionRecord:
        """
        Execute a job and return an ExecutionRecord.

        This is the new production interface that provides audit-grade records.

        Args:
            job_id: Unique job identifier
            job: Dictionary with code, input_data, timeout, job_type
            client_ip: Client IP address

        Returns:
            ExecutionRecord with complete audit trail
        """
        # Create initial record
        code = job.get("code", "")
        input_data = job.get("input_data", {})

        job_type_name = job.get("job_type") or "default"
        record = ExecutionRecord(
            execution_id=job_id,
            status="queued",
            job_type=job_type_name,
            job_ref=f"{job_type_name}@latest",
            created_at=datetime.now(timezone.utc),
            queued_at=datetime.now(timezone.utc),
            code_hash=sha256_content(code),
            input_hash=sha256_json(input_data),
            client_ip=client_ip,
            # Propagate idempotency and lineage fields from job data
            idempotency_key=job.get("idempotency_key"),
            idempotency_fingerprint=job.get("idempotency_fingerprint"),
            parent_execution_id=job.get("parent_execution_id"),
            relationship=job.get("relationship"),
        )

        # Store code and input as internal artifacts for replay support
        replay_artifacts = self._store_replay_artifacts(job_id, code, input_data)
        record.input_artifacts.extend(replay_artifacts)

        # Get job type configuration
        try:
            job_type = self._get_job_type(job.get("job_type"))
            record.job_type = job_type.name
        except ValueError as e:
            # Job type not found in production mode
            record.status = "failed"
            record.ended_at = datetime.now(timezone.utc)
            record.error = ExecutionError(type="config_error", message=str(e))
            return record

        timeout = job.get("timeout", job_type.timeout)

        # Create temporary workspace
        workspace = Path(tempfile.mkdtemp(prefix=f"job-{job_id}-", dir=WORKSPACE_DIR))

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
            record.started_at = datetime.now(timezone.utc)

            # Execute in container with retry for transient failures
            start_time = time.time()
            timed_out = False
            retry_ctx = RetryContext(RetryConfig(
                max_attempts=self.config.max_retry_attempts,
                base_delay=self.config.retry_base_delay
            ))

            while retry_ctx.should_retry():
                try:
                    result = self._run_container(
                        code_dir=code_dir,
                        input_dir=input_dir,
                        output_dir=output_dir,
                        timeout=timeout,
                        job_type=job_type,
                        extra_requirements=job.get("requirements"),
                    )

                    # Check for transient Docker errors in result
                    if not result.get("success") and result.get("error"):
                        error_msg = result.get("error", "").lower()
                        if any(pattern in error_msg for pattern in [
                            "circuit breaker",
                            "docker daemon",
                            "connection refused",
                        ]):
                            # Transient error, retry if possible
                            retry_ctx.record_failure(Exception(result.get("error")))
                            if retry_ctx.should_retry():
                                continue
                    break  # Success or non-transient error

                except subprocess.TimeoutExpired:
                    timed_out = True
                    result = {
                        "success": False,
                        "stdout": "",
                        "stderr": "",
                        "exit_code": -1,
                    }
                    break  # Timeout is not retriable

                except Exception as e:
                    if is_transient_error(e):
                        retry_ctx.record_failure(e)
                        if retry_ctx.should_retry():
                            continue
                    # Non-transient or exhausted retries
                    raise

            end_time = time.time()
            wall_time_ms = int((end_time - start_time) * 1000)

            # Update record with results
            record.ended_at = datetime.now(timezone.utc)
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
            record.artifacts = self._collect_artifacts(output_dir, job_id)

            # Read main JSON result (if present)
            output_file = output_dir / "result.json"
            if output_file.exists():
                try:
                    record.result_json = json.loads(output_file.read_text(encoding="utf-8"))
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
                record.status = "succeeded"
            else:
                record.status = "failed"
                error_type, error_msg = classify_error(
                    result.get("exit_code", 1),
                    result.get("stderr", ""),
                    timed_out
                )
                record.error = ExecutionError(type=error_type, message=error_msg)

            return record

        except Exception as e:
            # Unexpected error
            record.status = "failed"
            record.ended_at = datetime.now(timezone.utc)
            record.error = ExecutionError(
                type="internal_error",
                message=sanitize_error(str(e))
            )
            return record

        finally:
            # Cleanup workspace with error logging
            try:
                shutil.rmtree(workspace)
            except Exception as cleanup_err:
                logger.error(
                    "Failed to cleanup workspace %s: %s. "
                    "Manual cleanup may be required.",
                    workspace, cleanup_err
                )

    def _collect_artifacts(self, output_dir: Path, job_id: str) -> List[Artifact]:
        """
        Collect artifacts from output directory.

        Args:
            output_dir: Path to output directory
            job_id: Job ID for storage key generation

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

            # Validate filename is safe (no path traversal, no hidden files)
            if not is_safe_filename(path.name):
                logger.warning("Artifact %s has unsafe filename, skipping", path.name)
                continue

            size = path.stat().st_size

            # Check individual file size limit
            if size > self.config.max_artifact_bytes:
                logger.warning("Artifact %s exceeds size limit, skipping", path.name)
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
                    storage_key=f"runs/{job_id}/artifacts/{path.name}"
                ))
                total_size += size
            except Exception as e:
                logger.warning("Failed to process artifact %s: %s", path.name, e)

        return artifacts

    def _store_replay_artifacts(
        self,
        execution_id: str,
        code: str,
        input_data: dict
    ) -> List[InputArtifact]:
        """
        Store code and input_data as internal artifacts for replay support.

        These internal artifacts (prefixed with _) enable the rerun/fork
        functionality by preserving the exact code and inputs used.

        Args:
            execution_id: Unique execution identifier
            code: Python code that was executed
            input_data: Input data dictionary

        Returns:
            List of InputArtifact objects for the stored files
        """
        replay_artifacts = []
        runs_dir = self.config.data_dir / "runs" / execution_id

        try:
            # Ensure directory exists
            runs_dir.mkdir(parents=True, exist_ok=True)

            # Store code as _code.py
            code_bytes = code.encode("utf-8")
            code_path = runs_dir / "_code.py"
            code_path.write_text(code, encoding="utf-8")
            replay_artifacts.append(InputArtifact(
                name="_code.py",
                size_bytes=len(code_bytes),
                sha256=sha256_content(code),
                content_type="text/x-python",
                storage_key=f"runs/{execution_id}/_code.py",
            ))

            # Store input_data as _input.json (canonical form)
            input_json = json.dumps(input_data, sort_keys=True, separators=(",", ":"))
            input_bytes = input_json.encode("utf-8")
            input_path = runs_dir / "_input.json"
            input_path.write_text(input_json, encoding="utf-8")
            replay_artifacts.append(InputArtifact(
                name="_input.json",
                size_bytes=len(input_bytes),
                sha256=sha256_json(input_data),
                content_type="application/json",
                storage_key=f"runs/{execution_id}/_input.json",
            ))

            logger.debug("Stored replay artifacts for execution %s", execution_id)

        except Exception as e:
            logger.warning("Failed to store replay artifacts for %s: %s", execution_id, e)
            # Don't fail the execution if replay artifact storage fails

        return replay_artifacts

    def _run_container(
        self,
        code_dir: Path,
        input_dir: Path,
        output_dir: Path,
        timeout: int,
        job_type: JobType,
        extra_requirements: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Run Docker container with security restrictions.

        Args:
            code_dir: Path to directory containing code (will be mounted read-only)
            input_dir: Path to directory containing input data (will be mounted read-only)
            output_dir: Path to directory for output (will be mounted read-write)
            timeout: Timeout in seconds
            job_type: Job type configuration for container settings
            extra_requirements: Additional requirements to install (merged with job_type)

        Returns:
            Dictionary with execution results
        """
        # Check circuit breaker before attempting Docker operation
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available:
            return {
                "success": False,
                "error": "Docker service unavailable (circuit breaker open)",
                "stdout": "",
                "stderr": "Circuit breaker is open due to repeated Docker failures. Service will retry automatically.",
                "exit_code": -1
            }

        # Determine which image to use
        # Use custom base_image if specified, otherwise use default executor
        # Dependencies are installed at runtime via TAKO_REQUIREMENTS env var
        if job_type.base_image:
            if not validate_docker_image(job_type.base_image):
                return {
                    "success": False,
                    "error": "Invalid base_image configuration",
                    "stdout": "",
                    "stderr": f"base_image '{job_type.base_image}' failed validation",
                    "exit_code": -1
                }
            image_name = job_type.base_image
        else:
            image_name = self.docker_image

        # Merge job_type requirements with extra_requirements
        all_requirements = list(job_type.requirements) if job_type.requirements else []
        if extra_requirements:
            all_requirements.extend(extra_requirements)

        # Check if runtime deps require network access
        has_runtime_deps = bool(all_requirements)
        needs_network_for_deps = has_runtime_deps and not job_type.network_enabled

        if needs_network_for_deps:
            logger.warning(
                f"Job type '{job_type.name}' has requirements but network_enabled=false. "
                "Using bridge network for dependency installation. "
                "For true network isolation, use pre-built images via 'tako-vm build'."
            )

        cmd = [
            "docker", "run",
            "--rm",
            "--init",  # Faster signal handling with tini
            "--read-only",
            "--cap-drop=ALL",
            "--cap-add=SETUID",  # Required for gosu to switch user
            "--cap-add=SETGID",  # Required for gosu to switch user
            "--security-opt=no-new-privileges",
        ]

        # Mount uv cache volume for faster repeated installs
        if has_runtime_deps:
            cmd.append(f"--mount=type=volume,source={UV_CACHE_VOLUME},target=/root/.cache/uv")

        # Network isolation (default: no network for security)
        if job_type.network_enabled:
            cmd.append("--network=bridge")
        elif needs_network_for_deps:
            # Runtime deps need network access even if job wants isolation
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
            # Use larger /tmp and allow exec when installing packages (packages go to /tmp/site-packages)
            f"--tmpfs=/tmp:rw,{'exec' if has_runtime_deps else 'noexec'},nosuid,size={'300m' if has_runtime_deps else limits.tmpfs_size}",
        ])

        # Add seccomp profile if enabled and exists
        if self.config.enable_seccomp and self.config.seccomp_profile_path:
            if self.config.seccomp_profile_path.exists():
                cmd.append(f"--security-opt=seccomp={self.config.seccomp_profile_path}")

        # Add environment variables from job type (with validation)
        for key, value in job_type.environment.items():
            if not validate_env_key(key):
                logger.warning(f"Skipping invalid environment variable key: {key}")
                continue
            if not validate_env_value(value):
                logger.warning(f"Skipping environment variable with unsafe value: {key}")
                continue
            cmd.append(f"--env={key}={value}")

        # Pass requirements for runtime installation via uv
        if all_requirements:
            # Check requirements limit to prevent env var overflow
            if len(all_requirements) > MAX_REQUIREMENTS:
                logger.error(
                    f"Job has {len(all_requirements)} requirements "
                    f"(max {MAX_REQUIREMENTS}). Use pre-built images for large dependency sets."
                )
                return {
                    "success": False,
                    "error": f"Too many requirements ({len(all_requirements)} > {MAX_REQUIREMENTS})",
                    "stdout": "",
                    "stderr": "Use pre-built images for jobs with many dependencies",
                    "exit_code": -1
                }

            validated_reqs = []
            for req in all_requirements:
                if validate_pip_requirement(req):
                    validated_reqs.append(req)
                else:
                    logger.warning(f"Skipping invalid pip requirement: {req}")
            if validated_reqs:
                reqs_str = ",".join(validated_reqs)
                cmd.append(f"--env=TAKO_REQUIREMENTS={reqs_str}")

        # Add image name
        cmd.append(image_name)

        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False
            )

            # Record success with circuit breaker
            circuit_breaker.record_success()

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode
            }

        except subprocess.TimeoutExpired:
            # Timeout is not a Docker failure, don't record with circuit breaker
            return {
                "success": False,
                "error": f"Execution timeout exceeded ({timeout}s)",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
                "timeout": timeout
            }
        except FileNotFoundError:
            # Docker command not found - record failure
            circuit_breaker.record_failure("docker command not found")
            return {
                "success": False,
                "error": "Docker command not found",
                "stdout": "",
                "stderr": "",
                "exit_code": -1
            }
        except Exception as e:
            # Other errors might be Docker-related
            error_msg = str(e).lower()
            if "docker" in error_msg or "daemon" in error_msg or "connection" in error_msg:
                circuit_breaker.record_failure(str(e))
            # Sanitize error message before returning to prevent info leakage
            return {
                "success": False,
                "error": sanitize_error(str(e)),
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
