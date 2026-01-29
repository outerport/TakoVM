# Custom Libraries

Place `.whl` files here to install them in the executor image.

## Usage

1. Build your library as a wheel:
   ```bash
   python -m build --wheel
   ```

2. Copy to this directory:
   ```bash
   cp dist/your_lib-1.0.0-py3-none-any.whl /path/to/tako_vm/custom_libs/
   ```

3. Rebuild the executor image:
   ```bash
   docker build -t code-executor:latest -f docker/Dockerfile.executor .
   ```

See [docs/guide/custom-libraries.md](../../docs/guide/custom-libraries.md) for full documentation.
