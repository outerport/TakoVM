"""Tests for job type schema and merge behavior."""

import json
import tempfile
from pathlib import Path

from tako_vm.config import JobTypeConfig, JobTypeGPUConfig
from tako_vm.job_types import JobType, JobTypeRegistry, merge_config_job_types


def test_job_type_from_dict_reads_nested_gpu_config():
    """from_dict supports nested gpu object in JSON config."""
    job_type = JobType.from_dict(
        {
            "name": "gpu-job",
            "session_enabled": True,
            "gpu": {
                "enabled": True,
                "vendor": "nvidia",
                "count": 2,
                "device_ids": [],
            },
        }
    )

    assert job_type.name == "gpu-job"
    assert job_type.session_enabled is True
    assert job_type.gpu_enabled is True
    assert job_type.gpu_vendor == "nvidia"
    assert job_type.gpu_count == 2


def test_job_type_from_dict_supports_legacy_gpu_top_level_fields():
    """Top-level gpu_* fields remain supported for compatibility."""
    job_type = JobType.from_dict(
        {
            "name": "legacy-gpu",
            "gpu_enabled": True,
            "gpu_vendor": "amd",
            "gpu_device_ids": ["0", "1"],
        }
    )

    assert job_type.gpu_enabled is True
    assert job_type.gpu_vendor == "amd"
    assert job_type.gpu_device_ids == ["0", "1"]


def test_job_type_config_roundtrip_preserves_gpu_and_session_fields():
    """Dataclass <-> pydantic conversion preserves new fields."""
    original = JobType(
        name="roundtrip",
        session_enabled=True,
        gpu_enabled=True,
        gpu_vendor="nvidia",
        gpu_count=1,
        gpu_device_ids=[],
    )

    config_model = original.to_config()
    restored = JobType.from_config(config_model)

    assert restored.name == "roundtrip"
    assert restored.session_enabled is True
    assert restored.gpu_enabled is True
    assert restored.gpu_vendor == "nvidia"
    assert restored.gpu_count == 1


def test_merge_config_job_types_overrides_in_memory_without_persistence():
    """Config merge updates runtime registry without rewriting job_types.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "job_types.json"
        config_path.write_text(
            json.dumps(
                {
                    "job_types": [
                        {
                            "name": "default",
                            "timeout": 30,
                            "session_enabled": False,
                            "gpu": {
                                "enabled": False,
                                "vendor": None,
                                "count": None,
                                "device_ids": [],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        registry = JobTypeRegistry(config_path=config_path)
        original_on_disk = config_path.read_text(encoding="utf-8")

        merged_count = merge_config_job_types(
            registry,
            [
                JobTypeConfig(
                    name="default",
                    timeout=99,
                    session_enabled=True,
                    gpu=JobTypeGPUConfig(enabled=True, vendor="nvidia", count=1),
                )
            ],
        )

        assert merged_count == 1
        merged = registry.get("default")
        assert merged is not None
        assert merged.timeout == 99
        assert merged.session_enabled is True
        assert merged.gpu_enabled is True
        assert merged.gpu_vendor == "nvidia"

        # Persist=False means disk config should be untouched.
        assert config_path.read_text(encoding="utf-8") == original_on_disk
