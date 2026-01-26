"""
Job queue and worker pool for Tako VM.

Provides async job submission, execution, and cancellation.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Callable, Any, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
import uuid

from tako_vm.models import ExecutionRecord
from tako_vm.storage import ExecutionStorage

if TYPE_CHECKING:
    from tako_vm.execution.worker import CodeExecutor

logger = logging.getLogger(__name__)


@dataclass
class QueuedJob:
    """Job waiting in or being processed by the queue."""

    job_id: str
    """Unique job identifier."""

    job_data: dict
    """Job data including code, input_data, etc."""

    api_key_id: Optional[str]
    """API key that submitted this job."""

    client_ip: Optional[str]
    """Client IP address."""

    created_at: datetime = field(default_factory=datetime.utcnow)
    """When the job was queued."""

    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())
    """Future that will hold the result."""

    cancelled: bool = False
    """Whether this job has been cancelled."""


class WorkerPool:
    """
    Async worker pool for job execution.

    Uses asyncio.Queue for pending jobs and ThreadPoolExecutor
    for running Docker operations.
    """

    def __init__(
        self,
        executor: 'CodeExecutor',
        storage: ExecutionStorage,
        max_workers: int = 4,
        max_queue_size: int = 100
    ):
        """
        Initialize worker pool.

        Args:
            executor: CodeExecutor instance for running jobs
            storage: ExecutionStorage for persisting records
            max_workers: Maximum concurrent workers
            max_queue_size: Maximum pending jobs in queue
        """
        self.executor = executor
        self.storage = storage
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size

        self._queue: asyncio.Queue[QueuedJob] = asyncio.Queue(maxsize=max_queue_size)
        self._workers: list[asyncio.Task] = []
        self._active_jobs: Dict[str, QueuedJob] = {}
        self._running_jobs: Dict[str, QueuedJob] = {}
        self._thread_pool: Optional[ThreadPoolExecutor] = None
        self._shutdown = False
        self._started = False

    async def start(self) -> None:
        """Start worker tasks."""
        if self._started:
            return

        self._shutdown = False
        self._thread_pool = ThreadPoolExecutor(max_workers=self.max_workers)

        for i in range(self.max_workers):
            worker = asyncio.create_task(
                self._worker_loop(i),
                name=f"worker-{i}"
            )
            self._workers.append(worker)

        self._started = True
        logger.info(f"Started worker pool with {self.max_workers} workers")

    async def stop(self, timeout: float = 30.0) -> None:
        """
        Graceful shutdown.

        Args:
            timeout: Maximum time to wait for workers to finish
        """
        if not self._started:
            return

        self._shutdown = True

        # Cancel all pending jobs
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
                if not job.future.done():
                    job.future.cancel()
            except asyncio.QueueEmpty:
                break

        # Wait for workers with timeout
        if self._workers:
            done, pending = await asyncio.wait(
                self._workers,
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED
            )

            # Cancel any still running
            for task in pending:
                task.cancel()

        # Shutdown thread pool
        if self._thread_pool:
            self._thread_pool.shutdown(wait=False)
            self._thread_pool = None

        self._workers.clear()
        self._active_jobs.clear()
        self._running_jobs.clear()
        self._started = False

        logger.info("Worker pool stopped")

    async def submit(
        self,
        job_data: dict,
        api_key_id: Optional[str] = None,
        client_ip: Optional[str] = None
    ) -> str:
        """
        Submit job to queue.

        Args:
            job_data: Job data dictionary
            api_key_id: API key that submitted the job
            client_ip: Client IP address

        Returns:
            Job ID

        Raises:
            RuntimeError: If queue is full
        """
        job_id = str(uuid.uuid4())

        job = QueuedJob(
            job_id=job_id,
            job_data=job_data,
            api_key_id=api_key_id,
            client_ip=client_ip,
        )

        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise RuntimeError("Job queue is full, try again later")

        self._active_jobs[job_id] = job
        logger.info(f"Job {job_id} queued (queue size: {self._queue.qsize()})")

        return job_id

    async def wait_for_result(
        self,
        job_id: str,
        timeout: Optional[float] = None
    ) -> ExecutionRecord:
        """
        Wait for job completion.

        Args:
            job_id: Job ID to wait for
            timeout: Maximum time to wait (None = no timeout)

        Returns:
            ExecutionRecord with results

        Raises:
            KeyError: If job not found
            asyncio.TimeoutError: If timeout exceeded
            asyncio.CancelledError: If job was cancelled
        """
        job = self._active_jobs.get(job_id) or self._running_jobs.get(job_id)
        if not job:
            # Check if already completed in storage
            record = self.storage.get_record(job_id)
            if record:
                return record
            raise KeyError(f"Job {job_id} not found")

        if timeout:
            return await asyncio.wait_for(job.future, timeout=timeout)
        else:
            return await job.future

    def get_job_status(self, job_id: str) -> Optional[dict]:
        """
        Get current job status.

        Args:
            job_id: Job ID to check

        Returns:
            Status dict or None if not found
        """
        # Check active/running jobs
        if job_id in self._running_jobs:
            return {
                'job_id': job_id,
                'status': 'running',
                'created_at': self._running_jobs[job_id].created_at.isoformat(),
            }

        if job_id in self._active_jobs:
            job = self._active_jobs[job_id]
            if job.future.done():
                return {
                    'job_id': job_id,
                    'status': 'completed',
                    'created_at': job.created_at.isoformat(),
                }
            return {
                'job_id': job_id,
                'status': 'pending',
                'created_at': job.created_at.isoformat(),
                'queue_position': self._estimate_queue_position(job_id),
            }

        # Check storage for completed jobs
        record = self.storage.get_record(job_id)
        if record:
            return {
                'job_id': job_id,
                'status': record.status,
                'created_at': record.created_at.isoformat(),
                'duration_ms': record.duration_ms,
            }

        return None

    async def cancel(self, job_id: str) -> bool:
        """
        Cancel a queued or running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if job was found and cancelled
        """
        # Check pending jobs
        job = self._active_jobs.get(job_id)
        if job and not job.future.done():
            job.cancelled = True
            job.future.cancel()
            logger.info(f"Job {job_id} cancelled (was pending)")
            return True

        # Check running jobs - can't cancel Docker execution directly
        # but we can mark it for cleanup
        job = self._running_jobs.get(job_id)
        if job:
            job.cancelled = True
            logger.info(f"Job {job_id} marked for cancellation (was running)")
            return True

        return False

    def _estimate_queue_position(self, job_id: str) -> int:
        """Estimate position in queue (rough estimate)."""
        position = 1
        for jid in self._active_jobs:
            if jid == job_id:
                break
            if jid not in self._running_jobs:
                position += 1
        return position

    @property
    def stats(self) -> dict:
        """Get current pool statistics."""
        return {
            'pending': self._queue.qsize(),
            'running': len(self._running_jobs),
            'max_workers': self.max_workers,
            'max_queue_size': self.max_queue_size,
        }

    async def _worker_loop(self, worker_id: int) -> None:
        """
        Worker coroutine that processes jobs.

        Args:
            worker_id: Worker identifier for logging
        """
        logger.info(f"Worker {worker_id} started")

        while not self._shutdown:
            try:
                # Wait for job with timeout so we can check shutdown flag
                try:
                    job = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Skip if already cancelled
                if job.cancelled or job.future.cancelled():
                    self._active_jobs.pop(job.job_id, None)
                    continue

                # Move to running
                self._running_jobs[job.job_id] = job

                logger.info(f"Worker {worker_id} executing job {job.job_id}")

                try:
                    # Run execution in thread pool
                    record = await self._execute_job(job)

                    # Save to storage
                    self.storage.save_record(record)

                    # Set result
                    if not job.future.done():
                        job.future.set_result(record)

                except asyncio.CancelledError:
                    # Job was cancelled
                    record = ExecutionRecord(
                        execution_id=job.job_id,
                        status="cancelled",
                        job_type=job.job_data.get("job_type", "default"),
                        code_hash=ExecutionRecord.hash_content(job.job_data.get("code", "")),
                        input_hash=ExecutionRecord.hash_content(str(job.job_data.get("input_data", {}))),
                        api_key_id=job.api_key_id,
                        client_ip=job.client_ip,
                    )
                    self.storage.save_record(record)

                    if not job.future.done():
                        job.future.set_result(record)

                except Exception as e:
                    logger.error(f"Worker {worker_id} error on job {job.job_id}: {e}")

                    if not job.future.done():
                        job.future.set_exception(e)

                finally:
                    # Cleanup
                    self._active_jobs.pop(job.job_id, None)
                    self._running_jobs.pop(job.job_id, None)

            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
                await asyncio.sleep(1)  # Prevent tight loop on errors

        logger.info(f"Worker {worker_id} stopped")

    async def _execute_job(self, job: QueuedJob) -> ExecutionRecord:
        """
        Execute job in thread pool.

        Args:
            job: Job to execute

        Returns:
            ExecutionRecord with results
        """
        loop = asyncio.get_event_loop()

        # Run synchronous execution in thread pool
        record = await loop.run_in_executor(
            self._thread_pool,
            self._run_job_sync,
            job
        )

        return record

    def _run_job_sync(self, job: QueuedJob) -> ExecutionRecord:
        """
        Synchronously execute job (runs in thread pool).

        Args:
            job: Job to execute

        Returns:
            ExecutionRecord with results
        """
        return self.executor.execute_job_with_record(
            job_id=job.job_id,
            job=job.job_data,
            api_key_id=job.api_key_id,
            client_ip=job.client_ip,
        )
