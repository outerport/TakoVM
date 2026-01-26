FROM python:3.11-slim

# Install custom libraries
# Place your .whl files in tako_vm/custom_libs/ directory before building
COPY ./tako_vm/custom_libs /tmp/custom_libs

# Install any .whl files found in custom_libs
RUN if [ -n "$(ls -A /tmp/custom_libs/*.whl 2>/dev/null)" ]; then \
        pip install --no-cache-dir /tmp/custom_libs/*.whl; \
    fi && \
    rm -rf /tmp/custom_libs

# Security hardening: Create non-root user
RUN useradd -m -u 1000 sandbox && \
    mkdir -p /code /input /output /tmp && \
    chown sandbox:sandbox /output /tmp

# Set permissions
RUN chmod 755 /code /input && \
    chmod 777 /output /tmp

# Switch to non-root user
USER sandbox

# Working directory
WORKDIR /app

# Entrypoint - execute code from /code/main.py
ENTRYPOINT ["python", "-u", "/code/main.py"]
