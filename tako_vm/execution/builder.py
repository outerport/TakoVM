"""
Container Builder - Build Docker images for job types.

This module handles building pre-configured Docker containers
for each job type with their required dependencies.

Example:
    from container_builder import ContainerBuilder
    from job_types import JobType

    builder = ContainerBuilder()
    builder.build(JobType(
        name="data-processing",
        requirements=["pandas", "numpy"],
    ))
"""

import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional
import logging

from tako_vm.job_types import JobType, JobTypeRegistry

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """Raised when container build fails."""
    pass


class ContainerBuilder:
    """
    Builds Docker images for job types.
    """

    def __init__(self, custom_libs_path: Optional[Path] = None):
        """
        Initialize the builder.

        Args:
            custom_libs_path: Path to custom libraries directory
        """
        self.custom_libs_path = custom_libs_path or Path("custom_libs")

    def generate_dockerfile(self, job_type: JobType) -> str:
        """
        Generate Dockerfile content for a job type.

        Args:
            job_type: Job type configuration

        Returns:
            Dockerfile content as string
        """
        base_image = job_type.base_image or f"python:{job_type.python_version}-slim"

        # Build requirements install command
        requirements_cmd = ""
        if job_type.requirements:
            req_list = " ".join(f'"{r}"' for r in job_type.requirements)
            requirements_cmd = f"RUN pip install --no-cache-dir {req_list}"

        # Build environment variables
        env_lines = ""
        if job_type.environment:
            for key, value in job_type.environment.items():
                env_lines += f'ENV {key}="{value}"\n'

        dockerfile = f'''# Auto-generated Dockerfile for job type: {job_type.name}
FROM {base_image}

# Install custom libraries if present
COPY ./custom_libs /tmp/custom_libs
RUN if [ -n "$(ls -A /tmp/custom_libs/*.whl 2>/dev/null)" ]; then \\
        pip install --no-cache-dir /tmp/custom_libs/*.whl; \\
    fi && \\
    rm -rf /tmp/custom_libs

# Install job type requirements
{requirements_cmd}

# Copy shared code if present
COPY ./shared_code /app/shared_code
ENV PYTHONPATH="/app/shared_code:$PYTHONPATH"

# Environment variables
{env_lines}

# Create non-root user for security
RUN useradd -m -u 1000 sandbox && \\
    mkdir -p /code /input /output /tmp && \\
    chown sandbox:sandbox /output /tmp

# Set permissions
RUN chmod 755 /code /input && \\
    chmod 777 /output /tmp

WORKDIR /app

# Run as non-root user
USER sandbox

# Entry point
CMD ["python", "-u", "/code/main.py"]
'''
        return dockerfile

    def build(
        self,
        job_type: JobType,
        no_cache: bool = False,
        quiet: bool = False
    ) -> bool:
        """
        Build Docker image for a job type.

        Args:
            job_type: Job type configuration
            no_cache: If True, build without Docker cache
            quiet: If True, suppress build output

        Returns:
            True if build succeeded

        Raises:
            BuildError: If build fails
        """
        logger.info(f"Building container for job type: {job_type.name}")

        # Create temporary build context
        with tempfile.TemporaryDirectory() as build_dir:
            build_path = Path(build_dir)

            # Write Dockerfile
            dockerfile_content = self.generate_dockerfile(job_type)
            dockerfile_path = build_path / "Dockerfile"
            dockerfile_path.write_text(dockerfile_content)

            # Copy custom_libs
            custom_libs_dest = build_path / "custom_libs"
            custom_libs_dest.mkdir()
            if self.custom_libs_path.exists():
                for item in self.custom_libs_path.iterdir():
                    if item.is_file():
                        shutil.copy2(item, custom_libs_dest)

            # Copy shared code
            shared_code_dest = build_path / "shared_code"
            shared_code_dest.mkdir()
            for code_path in job_type.shared_code:
                src = Path(code_path)
                if src.exists():
                    if src.is_file():
                        shutil.copy2(src, shared_code_dest)
                    else:
                        shutil.copytree(src, shared_code_dest / src.name)

            # Build the image
            cmd = ["docker", "build", "-t", job_type.image_name]
            if no_cache:
                cmd.append("--no-cache")
            cmd.append(str(build_path))

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=not quiet,
                    text=True,
                    check=True
                )
                logger.info(f"Successfully built image: {job_type.image_name}")
                return True
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr if e.stderr else str(e)
                logger.error(f"Failed to build image: {error_msg}")
                raise BuildError(f"Failed to build {job_type.name}: {error_msg}")

    def build_all(
        self,
        registry: JobTypeRegistry,
        no_cache: bool = False,
        quiet: bool = False
    ) -> dict[str, bool]:
        """
        Build images for all registered job types.

        Args:
            registry: Job type registry
            no_cache: If True, build without Docker cache
            quiet: If True, suppress build output

        Returns:
            Dict mapping job type name to build success
        """
        results = {}
        for job_type in registry.list():
            try:
                self.build(job_type, no_cache=no_cache, quiet=quiet)
                results[job_type.name] = True
            except BuildError as e:
                logger.error(f"Failed to build {job_type.name}: {e}")
                results[job_type.name] = False
        return results

    def image_exists(self, job_type: JobType) -> bool:
        """
        Check if image for job type exists.

        Args:
            job_type: Job type configuration

        Returns:
            True if image exists
        """
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", job_type.image_name],
                capture_output=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def remove_image(self, job_type: JobType) -> bool:
        """
        Remove image for job type.

        Args:
            job_type: Job type configuration

        Returns:
            True if removed successfully
        """
        try:
            subprocess.run(
                ["docker", "rmi", job_type.image_name],
                capture_output=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False


def build_job_type_cli():
    """Command-line interface for building job types."""
    import argparse

    parser = argparse.ArgumentParser(description="Build job type containers")
    parser.add_argument("name", nargs="?", help="Job type name to build (or 'all')")
    parser.add_argument("--list", action="store_true", help="List all job types")
    parser.add_argument("--no-cache", action="store_true", help="Build without cache")
    parser.add_argument("--init-defaults", action="store_true", help="Initialize default job types")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    from tako_vm.job_types import init_default_job_types

    registry = JobTypeRegistry()

    if args.init_defaults:
        init_default_job_types(registry)
        print("Initialized default job types")

    if args.list:
        print("\nRegistered job types:")
        for jt in registry.list():
            status = "✓" if ContainerBuilder().image_exists(jt) else "✗"
            print(f"  [{status}] {jt.name}: {jt.requirements}")
        return

    if args.name:
        builder = ContainerBuilder()

        if args.name == "all":
            results = builder.build_all(registry, no_cache=args.no_cache)
            print("\nBuild results:")
            for name, success in results.items():
                status = "✓" if success else "✗"
                print(f"  [{status}] {name}")
        else:
            job_type = registry.get(args.name)
            if not job_type:
                print(f"Job type '{args.name}' not found")
                return
            builder.build(job_type, no_cache=args.no_cache)


if __name__ == "__main__":
    build_job_type_cli()
