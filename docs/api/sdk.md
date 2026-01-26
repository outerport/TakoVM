# Python SDK Reference

The Tako VM Python SDK provides a typed interface for code execution.

## Installation

The SDK is included with Tako VM:

```python
import tako_vm
```

## Quick Start

```python
from dataclasses import dataclass
import tako_vm

tako_vm.configure("http://localhost:8000")

@dataclass
class Input:
    x: int
    y: int

@dataclass
class Output:
    result: int

def add(input: Input) -> Output:
    return Output(result=input.x + input.y)

result = tako_vm.send(add, Input(10, 20))
print(result.result)  # 30
```

---

## Functions

### `tako_vm.configure()`

Configure the default client.

```python
tako_vm.configure(
    base_url="http://localhost:8000",
    timeout=30
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | str | `"http://localhost:8000"` | Server URL |
| `timeout` | int | 30 | Default execution timeout |

---

### `tako_vm.send()`

Execute a typed function and return the result.

```python
result = tako_vm.send(func, input_data, timeout=None, job_type=None)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `func` | Callable | Yes | Function with type hints |
| `input_data` | dataclass | Yes | Input dataclass instance |
| `timeout` | int | No | Timeout in seconds |
| `job_type` | str | No | Environment name |

**Returns**: Output dataclass instance

**Raises**:
- `ValidationError`: Invalid input/output types
- `ExecutionError`: Execution failed

---

### `tako_vm.send_raw()`

Execute a function and return raw result (doesn't raise on failure).

```python
result = tako_vm.send_raw(func, input_data, timeout=None, job_type=None)
```

**Returns**: `ExecutionResult` object

---

### `tako_vm.list_job_types()`

List available environments.

```python
job_types = tako_vm.list_job_types()
for jt in job_types:
    print(jt["name"], jt["requirements"])
```

---

### `tako_vm.get_job_type()`

Get a specific environment.

```python
jt = tako_vm.get_job_type("data-processing")
print(jt["memory_limit"])  # "1g"
```

---

## Classes

### `TakoVM`

Client class for more control.

```python
from tako_vm import TakoVM

client = TakoVM(
    base_url="http://localhost:8000",
    timeout=60
)

result = client.send(my_func, my_input)
health = client.health()
```

#### Methods

| Method | Description |
|--------|-------------|
| `send(func, input)` | Execute function, return typed output |
| `send_raw(func, input)` | Execute function, return ExecutionResult |
| `health()` | Get server health status |
| `list_job_types()` | List environments |
| `get_job_type(name)` | Get environment config |

---

### `ExecutionResult`

Raw execution result.

```python
@dataclass
class ExecutionResult:
    success: bool
    output: any
    execution_time: float
    stdout: str
    stderr: str
    error: Optional[str]
    job_type: Optional[str]
```

---

## Exceptions

### `TakoVMError`

Base exception for all Tako VM errors.

```python
from tako_vm import TakoVMError

try:
    result = tako_vm.send(func, input)
except TakoVMError as e:
    print(f"Tako VM error: {e}")
```

---

### `ValidationError`

Raised when input/output validation fails.

```python
from tako_vm import ValidationError

try:
    result = tako_vm.send(func, "not a dataclass")
except ValidationError as e:
    print(f"Validation error: {e}")
```

---

### `ExecutionError`

Raised when code execution fails.

```python
from tako_vm import ExecutionError

try:
    result = tako_vm.send(func, input)
except ExecutionError as e:
    print(f"Execution failed: {e}")
    print(f"Stdout: {e.stdout}")
    print(f"Stderr: {e.stderr}")
```

---

## Type Requirements

### Input/Output Dataclasses

Functions must use dataclass type hints:

```python
from dataclasses import dataclass

@dataclass
class MyInput:
    name: str
    values: list
    count: int = 0  # Default values supported

@dataclass
class MyOutput:
    result: str
    processed: int

def my_func(input: MyInput) -> MyOutput:
    return MyOutput(
        result=f"Hello {input.name}",
        processed=len(input.values)
    )
```

### Supported Types

Fields must be JSON-serializable:

| Type | Supported |
|------|-----------|
| `str`, `int`, `float`, `bool` | ✓ |
| `list`, `dict` | ✓ |
| `None` / `Optional` | ✓ |
| `datetime` | ✗ (use ISO string) |
| Custom classes | ✗ |

---

## Examples

### Basic Usage

```python
from dataclasses import dataclass
import tako_vm

@dataclass
class Input:
    text: str

@dataclass
class Output:
    length: int
    words: int

def analyze(input: Input) -> Output:
    return Output(
        length=len(input.text),
        words=len(input.text.split())
    )

result = tako_vm.send(analyze, Input("Hello world"))
print(result.length)  # 11
print(result.words)   # 2
```

### With Environment

```python
@dataclass
class StatsInput:
    values: list

@dataclass
class StatsOutput:
    mean: float
    std: float

def compute_stats(input: StatsInput) -> StatsOutput:
    import numpy as np
    arr = np.array(input.values)
    return StatsOutput(
        mean=float(np.mean(arr)),
        std=float(np.std(arr))
    )

result = tako_vm.send(
    compute_stats,
    StatsInput([1, 2, 3, 4, 5]),
    job_type="data-processing"
)
```

### Error Handling

```python
from tako_vm import ExecutionError, ValidationError

try:
    result = tako_vm.send(func, input)
except ValidationError as e:
    print(f"Invalid input: {e}")
except ExecutionError as e:
    print(f"Execution failed: {e}")
    print(f"Output: {e.stdout}")
```
