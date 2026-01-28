"""
Example custom library for POC testing.

This demonstrates how custom libraries work in the execution environment.
"""


class DataClass:
    """Example data class for testing."""

    def __init__(self, data):
        """Initialize with data dictionary."""
        self.data = data

    @classmethod
    def from_dict(cls, d):
        """Create instance from dictionary."""
        return cls(d)

    def transform(self):
        """
        Example transformation - doubles numeric values.

        Returns:
            New DataClass instance with transformed data
        """
        transformed_data = {}
        for k, v in self.data.items():
            if isinstance(v, (int, float)):
                transformed_data[k] = v * 2
            else:
                transformed_data[k] = v
        return DataClass(transformed_data)

    def to_dict(self):
        """Convert to dictionary."""
        return self.data

    def __repr__(self):
        return f"DataClass({self.data})"
