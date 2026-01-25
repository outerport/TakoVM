# Custom Libraries

This directory contains custom Python libraries that will be installed in the Docker execution environment.

## Adding Libraries

### Option 1: Pre-built Wheel Files

1. Place `.whl` files directly in this directory
2. Rebuild the Docker image: `docker build -t code-executor:latest .`
3. Restart the API server

### Option 2: Build from Source (Like example_lib)

1. Create your library structure:
   ```
   custom_libs/
   └── your_library/
       ├── setup.py
       ├── your_library/
       │   └── __init__.py
   ```

2. Build the wheel:
   ```bash
   cd custom_libs/your_library
   python setup.py bdist_wheel
   cp dist/*.whl ../
   ```

3. Rebuild Docker image:
   ```bash
   docker build -t code-executor:latest .
   ```

## Example Library

The `example_lib` directory contains a simple example library that demonstrates:
- Basic class structure
- Data transformation methods
- How to import and use in generated code

### Building the Example Library

```bash
cd custom_libs/example_lib
python setup.py bdist_wheel
cp dist/example_lib-1.0.0-py3-none-any.whl ../
cd ../..
docker build -t code-executor:latest .
```

### Using the Example Library

In your generated code:

```python
import json
from example_lib import DataClass

with open('/input/data.json') as f:
    data = json.load(f)

obj = DataClass.from_dict(data)
transformed = obj.transform()

with open('/output/result.json', 'w') as f:
    json.dump(transformed.to_dict(), f)
```

## Notes

- Libraries are installed once during Docker image build
- To update a library, rebuild the Docker image
- All dependencies of your library must also be included or available via pip
- Libraries are available to all code executions
