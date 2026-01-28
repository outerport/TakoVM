"""
Example usage of tako_vm - typed function execution.

This demonstrates the typed interface where you define input/output
dataclasses and execute typed functions in isolated containers.

Run from project root: python examples/example_tako_vm.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tako_vm  # pylint: disable=wrong-import-position


# Define input/output types
@dataclass
class AddInput:
    x: int
    y: int


@dataclass
class AddOutput:
    result: int


@dataclass
class TransformInput:
    values: list
    multiplier: int


@dataclass
class TransformOutput:
    transformed: list
    count: int


@dataclass
class FibInput:
    n: int


@dataclass
class FibOutput:
    value: int
    sequence: list


# Define typed functions
def add_numbers(input: AddInput) -> AddOutput:
    """Simple addition function."""
    return AddOutput(result=input.x + input.y)


def transform_values(input: TransformInput) -> TransformOutput:
    """Transform a list of values."""
    transformed = [v * input.multiplier for v in input.values]
    return TransformOutput(transformed=transformed, count=len(transformed))


def fibonacci(input: FibInput) -> FibOutput:
    """Calculate fibonacci number and sequence."""

    def fib(n):
        if n <= 1:
            return n
        return fib(n - 1) + fib(n - 2)

    sequence = [fib(i) for i in range(input.n + 1)]
    return FibOutput(value=fib(input.n), sequence=sequence)


def main():
    print("=== Tako VM - Typed Function Execution ===\n")

    # Example 1: Simple addition
    print("Example 1: Add two numbers")
    result = tako_vm.send(add_numbers, AddInput(x=10, y=20))
    print("  Input: AddInput(x=10, y=20)")
    print(f"  Output: AddOutput(result={result.result})")
    print(f"  Type: {type(result).__name__}")
    print()

    # Example 2: Transform values
    print("Example 2: Transform a list")
    result = tako_vm.send(transform_values, TransformInput(values=[1, 2, 3, 4, 5], multiplier=3))
    print("  Input: TransformInput(values=[1,2,3,4,5], multiplier=3)")
    print(f"  Output: TransformOutput(transformed={result.transformed}, count={result.count})")
    print()

    # Example 3: Fibonacci
    print("Example 3: Fibonacci sequence")
    result = tako_vm.send(fibonacci, FibInput(n=10))
    print("  Input: FibInput(n=10)")
    print(f"  Output: FibOutput(value={result.value}, sequence={result.sequence})")
    print()

    # Example 4: Using send_raw for more control
    print("Example 4: Using send_raw for detailed result")
    raw_result = tako_vm.send_raw(add_numbers, AddInput(x=5, y=7))
    print(f"  Success: {raw_result.success}")
    print(f"  Output: {raw_result.output}")
    print(f"  Execution time: {raw_result.execution_time:.3f}s")
    print(f"  Stdout: {repr(raw_result.stdout)}")
    print()

    # Example 5: Error handling
    print("Example 5: Error handling")

    @dataclass
    class ErrorInput:
        x: int

    @dataclass
    class ErrorOutput:
        result: int

    def will_fail(input: ErrorInput) -> ErrorOutput:
        raise ValueError("Intentional error!")

    try:
        tako_vm.send(will_fail, ErrorInput(x=1))
    except tako_vm.ExecutionError as e:
        print(f"  Caught ExecutionError: {e}")
    print()

    print("=== All examples completed ===")


if __name__ == "__main__":
    main()
