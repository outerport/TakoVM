"""
Integration tests for Tako VM API.

Tests job submission, status checking, and result retrieval
using FastAPI's TestClient (no running server required).
"""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with lifespan management and temp database."""
    # Use a temporary database to avoid conflicts with existing data
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["TAKO_VM_DATA_DIR"] = tmpdir
        os.environ["TAKO_VM_DATABASE_FILE"] = os.path.join(tmpdir, "test.db")

        # Reset config to pick up new env vars
        from tako_vm.config import reset_config
        reset_config()

        # Import app after setting env vars
        from tako_vm.server.app import app

        with TestClient(app) as test_client:
            yield test_client

        # Cleanup
        reset_config()
        os.environ.pop("TAKO_VM_DATA_DIR", None)
        os.environ.pop("TAKO_VM_DATABASE_FILE", None)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_check(self, client):
        """Health endpoint returns status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "docker_available" in data
        assert "version" in data


class TestSyncExecution:
    """Tests for synchronous /execute endpoint."""

    def test_execute_simple_code(self, client):
        """Execute simple code that writes output."""
        code = '''
import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"sum": data["a"] + data["b"]}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
print("Done!")
'''
        response = client.post("/execute", json={
            "code": code,
            "input_data": {"a": 10, "b": 20}
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["output"] == {"sum": 30}
        assert "Done!" in data["stdout"]
        assert data["exit_code"] == 0

    def test_execute_syntax_error(self, client):
        """Execute code with syntax error."""
        response = client.post("/execute", json={
            "code": "def broken(",
            "input_data": {}
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["exit_code"] != 0

    def test_execute_empty_code_rejected(self, client):
        """Empty code is rejected."""
        response = client.post("/execute", json={
            "code": "",
            "input_data": {}
        })

        assert response.status_code == 422  # Validation error


class TestAsyncExecution:
    """Tests for async /execute/async endpoint."""

    def test_async_submit_returns_job_id(self, client):
        """Async submit returns job ID and queued status."""
        response = client.post("/execute/async", json={
            "code": "print('hello')",
            "input_data": {}
        })

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"

    def test_async_job_status(self, client):
        """Can check status of submitted job."""
        # Submit job
        submit_response = client.post("/execute/async", json={
            "code": "print('hello')",
            "input_data": {}
        })
        job_id = submit_response.json()["job_id"]

        # Check status
        status_response = client.get(f"/jobs/{job_id}")
        assert status_response.status_code == 200
        data = status_response.json()
        assert data["job_id"] == job_id
        assert "status" in data

    def test_async_job_result_wait(self, client):
        """Can wait for and retrieve job result."""
        code = '''
import json
with open("/output/result.json", "w") as f:
    json.dump({"message": "async complete"}, f)
'''
        # Submit job
        submit_response = client.post("/execute/async", json={
            "code": code,
            "input_data": {}
        })
        job_id = submit_response.json()["job_id"]

        # Wait for result
        result_response = client.get(
            f"/jobs/{job_id}/result",
            params={"wait": True, "timeout": 60}
        )

        assert result_response.status_code == 200
        data = result_response.json()
        assert data["execution_id"] == job_id
        # Status should be a terminal state
        assert data["status"] in ["succeeded", "failed", "timeout", "oom", "cancelled"]

    def test_job_not_found(self, client):
        """Non-existent job returns 404."""
        response = client.get("/jobs/nonexistent-job-id")
        assert response.status_code == 404


class TestJobTypes:
    """Tests for job type endpoints."""

    def test_list_job_types(self, client):
        """Can list available job types."""
        response = client.get("/job-types")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Should have at least the default job type
        names = [jt["name"] for jt in data]
        assert "default" in names

    def test_get_job_type(self, client):
        """Can get specific job type."""
        response = client.get("/job-types/default")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"
        assert "requirements" in data
        assert "python_version" in data

    def test_job_type_not_found(self, client):
        """Non-existent job type returns 404."""
        response = client.get("/job-types/nonexistent")
        assert response.status_code == 404


class TestExecutionRecords:
    """Tests for execution record endpoints."""

    def test_list_executions(self, client):
        """Can list execution records (paginated)."""
        response = client.get("/executions")
        assert response.status_code == 200
        data = response.json()
        # Response is now paginated
        assert "items" in data
        assert "limit" in data
        assert "offset" in data
        assert "has_more" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_list_executions_with_filters(self, client):
        """Can filter execution records."""
        response = client.get("/executions", params={
            "status": "succeeded",
            "limit": 10
        })
        assert response.status_code == 200


class TestPoolStats:
    """Tests for worker pool stats."""

    def test_pool_stats(self, client):
        """Can get pool statistics."""
        response = client.get("/pool/stats")
        assert response.status_code == 200
        data = response.json()
        assert "pending" in data
        assert "running" in data
        assert "max_workers" in data


class TestDLQ:
    """Tests for dead letter queue endpoints."""

    def test_dlq_stats(self, client):
        """Can get DLQ statistics."""
        response = client.get("/dlq/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "by_error_type" in data

    def test_dlq_list(self, client):
        """Can list DLQ entries (paginated)."""
        response = client.get("/dlq")
        assert response.status_code == 200
        data = response.json()
        # Response is now paginated
        assert "items" in data
        assert "limit" in data
        assert "offset" in data
        assert "has_more" in data
        assert "count" in data
        assert isinstance(data["items"], list)
