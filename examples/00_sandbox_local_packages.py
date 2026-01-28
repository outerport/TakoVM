"""
Tako VM Sandbox with Local Packages

This example shows how to use your own local Python packages
inside the sandbox without publishing them to PyPI.
"""

import os
import tempfile

from tako_vm import Sandbox

# Create a temporary package to demonstrate
with tempfile.TemporaryDirectory() as tmpdir:
    # Create a simple package structure
    pkg_dir = os.path.join(tmpdir, "my_utils")
    os.makedirs(pkg_dir)

    # Create __init__.py
    with open(os.path.join(pkg_dir, "__init__.py"), "w") as f:
        f.write("from .helpers import greet, calculate\n")

    # Create helpers.py
    with open(os.path.join(pkg_dir, "helpers.py"), "w") as f:
        f.write("""
def greet(name):
    return f"Hello, {name}!"

def calculate(a, b, operation="add"):
    if operation == "add":
        return a + b
    elif operation == "multiply":
        return a * b
    else:
        raise ValueError(f"Unknown operation: {operation}")
""")

    print(f"Created temporary package at: {pkg_dir}")
    print()

    # Use the package in the sandbox
    print("=== Using Local Package ===")
    sb = Sandbox(package_dirs=[pkg_dir])

    result = sb.run("""
from my_utils import greet, calculate

# Use functions from local package
print(greet("Tako VM"))
print(f"2 + 3 = {calculate(2, 3)}")
print(f"4 * 5 = {calculate(4, 5, 'multiply')}")
""")

    print(f"stdout:\n{result.stdout}")
    print(f"success: {result.success}")
