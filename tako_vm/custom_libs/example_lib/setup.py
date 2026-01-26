"""
Setup script for example custom library.

To build:
    python setup.py bdist_wheel

This will create a .whl file in dist/ that can be installed in the Docker image.
"""
from setuptools import setup, find_packages

setup(
    name="example_lib",
    version="1.0.0",
    description="Example custom library for secure code executor POC",
    packages=find_packages(),
    python_requires=">=3.11",
)
