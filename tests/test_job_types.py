"""
Test job types functionality.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dataclasses import dataclass
import tako_vm


@dataclass
class NumbersInput:
    values: list


@dataclass
class StatsOutput:
    sum: float
    mean: float
    count: int


def compute_stats(input: NumbersInput) -> StatsOutput:
    """Compute statistics using numpy (requires data-processing job type)."""
    import numpy as np
    arr = np.array(input.values)
    return StatsOutput(
        sum=float(np.sum(arr)),
        mean=float(np.mean(arr)),
        count=len(arr)
    )


@dataclass
class SimpleInput:
    x: int
    y: int


@dataclass
class SimpleOutput:
    result: int


def simple_add(input: SimpleInput) -> SimpleOutput:
    """Simple addition (works with default job type)."""
    return SimpleOutput(result=input.x + input.y)


def main():
    print("=== Testing Job Types ===\n")

    # Test 1: List available job types
    print("1. Available job types:")
    job_types = tako_vm.list_job_types()
    for jt in job_types:
        status = "✓" if jt["image_exists"] else "✗"
        print(f"   [{status}] {jt['name']}: {jt['requirements']}")
    print()

    # Test 2: Simple execution with default job type
    print("2. Simple execution (default job type):")
    result = tako_vm.send(simple_add, SimpleInput(x=10, y=20))
    print(f"   Input: SimpleInput(x=10, y=20)")
    print(f"   Output: {result}")
    print()

    # Test 3: Execution with data-processing job type
    print("3. NumPy execution (data-processing job type):")
    raw_result = tako_vm.send_raw(
        compute_stats,
        NumbersInput(values=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
        job_type="data-processing"
    )
    print(f"   Job type used: {raw_result.job_type}")
    print(f"   Success: {raw_result.success}")
    if raw_result.success:
        print(f"   Output: {raw_result.output}")
    else:
        print(f"   Error: {raw_result.error}")
        print(f"   Stderr: {raw_result.stderr}")
    print()

    print("=== Tests Complete ===")


if __name__ == "__main__":
    main()
