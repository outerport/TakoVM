"""
tako_vm - Typed function execution SDK for the secure code executor.

Provides a typed interface for executing functions in isolated containers:

    from dataclasses import dataclass
    import tako_vm

    @dataclass
    class InputStruct:
        args1: int
        args2: int

    @dataclass
    class OutputStruct:
        return1: int

    def my_func(input: InputStruct) -> OutputStruct:
        return OutputStruct(return1=input.args1 + input.args2)

    result = tako_vm.send(my_func, InputStruct(1, 2))
    print(result.return1)  # 3
"""

import inspect
import textwrap
from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from typing import Any, Callable, Optional, Type, TypeVar, get_type_hints

import requests

# Type variables for generic typing
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass
class ExecutionResult:
    """Result of a function execution."""

    success: bool
    output: Any
    execution_time: float
    stdout: str
    stderr: str
    error: Optional[str] = None
    job_type: Optional[str] = None


class TakoVMError(Exception):
    """Base exception for tako_vm errors."""


class ExecutionError(TakoVMError):
    """Raised when code execution fails."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class ValidationError(TakoVMError):
    """Raised when input/output validation fails."""


class TakoVM:
    """
    Client for executing typed functions in isolated containers.

    Example:
        client = TakoVM("http://localhost:8000")
        result = client.send(my_func, InputStruct(1, 2))
    """

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 30):
        """
        Initialize the TakoVM client.

        Args:
            base_url: URL of the code executor API
            timeout: Default execution timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.default_timeout = timeout

    def send(
        self,
        func: Callable[[InputT], OutputT],
        input_data: InputT,
        timeout: Optional[int] = None,
        job_type: Optional[str] = None,
    ) -> OutputT:
        """
        Execute a typed function in an isolated container.

        Args:
            func: Function to execute. Must have type hints for input and output.
            input_data: Input dataclass instance
            timeout: Execution timeout in seconds (uses job type default if not specified)
            job_type: Job type name (uses "default" if not specified)

        Returns:
            Output dataclass instance

        Raises:
            ValidationError: If input/output types are invalid
            ExecutionError: If execution fails
        """
        # Validate input is a dataclass
        if not is_dataclass(input_data):
            raise ValidationError(
                f"input_data must be a dataclass instance, got {type(input_data)}"
            )

        # Get type hints from function
        hints = get_type_hints(func)
        if "return" not in hints:
            raise ValidationError("Function must have a return type hint")

        output_type = hints["return"]
        if not is_dataclass(output_type):
            raise ValidationError(f"Return type must be a dataclass, got {output_type}")

        # Get input type from first parameter
        params = list(hints.keys())
        params.remove("return")
        if not params:
            raise ValidationError("Function must have at least one parameter")

        input_type = hints[params[0]]
        if not is_dataclass(input_type):
            raise ValidationError(f"Input parameter must be a dataclass type, got {input_type}")

        # Generate the wrapper code
        code = self._generate_code(func, input_type, output_type)

        # Serialize input
        input_dict = asdict(input_data)

        # Execute
        result = self._execute(code, input_dict, timeout, job_type)

        if not result.success:
            raise ExecutionError(
                result.error or "Execution failed", stdout=result.stdout, stderr=result.stderr
            )

        # Deserialize output
        try:
            return output_type(**result.output)
        except (TypeError, ValueError) as e:
            raise ValidationError(
                f"Failed to deserialize output to {output_type.__name__}: {e}"
            ) from e

    def send_raw(
        self,
        func: Callable[[InputT], OutputT],
        input_data: InputT,
        timeout: Optional[int] = None,
        job_type: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute a typed function and return the raw result.

        Same as send() but returns ExecutionResult instead of raising on failure.
        Useful when you want to handle errors yourself or inspect stdout/stderr.
        """
        if not is_dataclass(input_data):
            raise ValidationError(
                f"input_data must be a dataclass instance, got {type(input_data)}"
            )

        hints = get_type_hints(func)
        if "return" not in hints:
            raise ValidationError("Function must have a return type hint")

        output_type = hints["return"]
        params = [k for k in hints.keys() if k != "return"]
        input_type = hints[params[0]] if params else type(input_data)

        code = self._generate_code(func, input_type, output_type)
        input_dict = asdict(input_data)

        result = self._execute(code, input_dict, timeout, job_type)

        # Deserialize output if successful
        if result.success and result.output:
            try:
                result.output = output_type(**result.output)
            except (TypeError, ValueError, KeyError):
                # Keep as dict if deserialization fails (type mismatch, missing fields, etc.)
                pass

        return result

    def _generate_code(self, func: Callable, input_type: Type, output_type: Type) -> str:
        """Generate wrapper code for execution in the sandbox."""

        # Get function source
        func_source = inspect.getsource(func)
        func_source = textwrap.dedent(func_source)

        # Get dataclass definitions
        input_source = self._get_dataclass_source(input_type)
        output_source = self._get_dataclass_source(output_type)

        # Build the wrapper code
        code = f"""
from dataclasses import dataclass, asdict
import json

# Input dataclass definition
{input_source}

# Output dataclass definition
{output_source}

# User function
{func_source}

# Execution wrapper
def _execute():
    # Read input
    with open("/input/data.json") as f:
        input_dict = json.load(f)

    # Deserialize to input dataclass
    input_obj = {input_type.__name__}(**input_dict)

    # Execute function
    result = {func.__name__}(input_obj)

    # Serialize output
    output_dict = asdict(result)

    # Write output
    with open("/output/result.json", "w") as f:
        json.dump(output_dict, f)

_execute()
"""
        return code.strip()

    def _get_dataclass_source(self, cls: Type) -> str:
        """Get the source code for a dataclass."""
        try:
            source = inspect.getsource(cls)
            return textwrap.dedent(source)
        except (OSError, TypeError):
            # If we can't get source, generate it from fields
            return self._generate_dataclass_source(cls)

    def _generate_dataclass_source(self, cls: Type) -> str:
        """Generate dataclass source code from its fields."""
        lines = ["@dataclass", f"class {cls.__name__}:"]

        for field in fields(cls):
            type_name = self._get_type_name(field.type)
            if field.default_factory is MISSING:
                if field.default is not MISSING:
                    lines.append(f"    {field.name}: {type_name} = {repr(field.default)}")
                else:
                    lines.append(f"    {field.name}: {type_name}")
            else:
                lines.append(f"    {field.name}: {type_name}")

        return "\n".join(lines)

    def _get_type_name(self, t: Type) -> str:
        """Get a string representation of a type."""
        if hasattr(t, "__name__"):
            return t.__name__
        return str(t)

    def _execute(
        self, code: str, input_data: dict, timeout: Optional[int], job_type: Optional[str]
    ) -> ExecutionResult:
        """Execute code via the API."""
        try:
            payload = {
                "code": code,
                "input_data": input_data,
            }

            # Only include optional fields if specified
            if timeout is not None:
                payload["timeout"] = timeout
            if job_type is not None:
                payload["job_type"] = job_type

            # Use provided timeout or default for HTTP timeout
            http_timeout = (timeout or self.default_timeout) + 10

            response = requests.post(f"{self.base_url}/execute", json=payload, timeout=http_timeout)
            response.raise_for_status()
            data = response.json()

            return ExecutionResult(
                success=data.get("success", False),
                output=data.get("output"),
                execution_time=data.get("execution_time", 0),
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                error=data.get("error"),
                job_type=data.get("job_type"),
            )
        except requests.exceptions.RequestException as e:
            return ExecutionResult(
                success=False,
                output=None,
                execution_time=0,
                stdout="",
                stderr="",
                error=f"Request failed: {e}",
            )

    def health(self) -> dict:
        """Check API health status."""
        response = requests.get(f"{self.base_url}/health", timeout=10)
        response.raise_for_status()
        return response.json()

    def list_job_types(self) -> list:
        """
        List available job types.

        Returns:
            List of job type configurations
        """
        response = requests.get(f"{self.base_url}/job-types", timeout=10)
        response.raise_for_status()
        return response.json()

    def get_job_type(self, name: str) -> dict:
        """
        Get a specific job type by name.

        Args:
            name: Job type name

        Returns:
            Job type configuration
        """
        response = requests.get(f"{self.base_url}/job-types/{name}", timeout=10)
        response.raise_for_status()
        return response.json()


# Default client instance
_default_client: Optional[TakoVM] = None


def configure(base_url: str = "http://localhost:8000", timeout: int = 30):
    """
    Configure the default tako_vm client.

    Args:
        base_url: URL of the code executor API
        timeout: Default execution timeout in seconds
    """
    global _default_client
    _default_client = TakoVM(base_url=base_url, timeout=timeout)


def _get_client() -> TakoVM:
    """Get the default client, creating one if necessary."""
    global _default_client
    if _default_client is None:
        _default_client = TakoVM()
    return _default_client


def send(
    func: Callable[[InputT], OutputT],
    input_data: InputT,
    timeout: Optional[int] = None,
    job_type: Optional[str] = None,
) -> OutputT:
    """
    Execute a typed function in an isolated container.

    This is a convenience function using the default client.

    Args:
        func: Function to execute. Must have type hints for input and output.
        input_data: Input dataclass instance
        timeout: Execution timeout in seconds
        job_type: Job type name (e.g., "data-processing", "ml-inference")

    Returns:
        Output dataclass instance

    Example:
        @dataclass
        class Input:
            x: int
            y: int

        @dataclass
        class Output:
            result: int

        def add(input: Input) -> Output:
            return Output(result=input.x + input.y)

        result = tako_vm.send(add, Input(1, 2))
        print(result.result)  # 3

        # With job type
        result = tako_vm.send(process_data, data, job_type="data-processing")
    """
    return _get_client().send(func, input_data, timeout, job_type)


def send_raw(
    func: Callable[[InputT], OutputT],
    input_data: InputT,
    timeout: Optional[int] = None,
    job_type: Optional[str] = None,
) -> ExecutionResult:
    """
    Execute a typed function and return the raw result.

    Same as send() but returns ExecutionResult instead of raising on failure.
    """
    return _get_client().send_raw(func, input_data, timeout, job_type)


def list_job_types() -> list:
    """List available job types."""
    return _get_client().list_job_types()


def get_job_type(name: str) -> dict:
    """Get a specific job type by name."""
    return _get_client().get_job_type(name)
