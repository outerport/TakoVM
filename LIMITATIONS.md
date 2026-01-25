# System Limitations

Quick reference guide for known limitations and constraints.

## Resource Limits (Per Execution)

| Resource | Limit | Consequence if Exceeded |
|----------|-------|------------------------|
| Memory | 512MB | Process killed (OOM) |
| CPU | 1 core | Slower execution |
| Timeout | 30s (default), 300s (max) | Process terminated |
| Temp Storage | 100MB | Write fails (disk full) |
| Processes | 100 | Fork fails |
| Code Size | 100KB | Request rejected (400) |
| Input Data | 1MB | Request rejected (400) |

## What's NOT Supported (POC)

- ❌ **Network access** - All connections blocked (`--network=none`)
- ❌ **Database queries** - No DB connection available
- ❌ **File uploads/downloads** - No external I/O
- ❌ **Runtime package installation** - `pip install` fails (read-only filesystem)
- ❌ **Streaming responses** - Results only after completion
- ❌ **Job persistence** - Results lost after response
- ❌ **Authentication/Authorization** - Open access (anyone can call API)
- ❌ **Rate limiting** - Unlimited requests per client
- ❌ **Concurrent execution** - Sequential only (one job at a time)
- ❌ **State between executions** - Each job starts fresh

## Security Caveats

⚠️ **Container Isolation**: Not perfect, sophisticated exploits possible  
⚠️ **Shared Kernel**: All containers share host OS kernel  
⚠️ **Docker Socket**: Worker has Docker control (security risk if compromised)  
⚠️ **No Audit Logs**: Execution history not recorded (POC)  
⚠️ **Error Messages**: May leak filesystem paths in stderr  
⚠️ **No Encryption**: Temporary files stored in plaintext  

## Platform Support

| Platform | Support Level | Notes |
|----------|--------------|-------|
| Linux | ✅ Full | Primary target, recommended |
| macOS | ⚠️ Partial | Docker Desktop required, dev/test only |
| Windows | ❌ Limited | WSL2 required, not recommended |

## Performance Characteristics

### Expected Latency (Approximate)

- **Container spawn overhead**: 500ms - 2s
- **Simple execution** (< 1s code): 1.5s - 3s total
- **Medium execution** (5-10s code): 6s - 12s total
- **Maximum throughput**: ~2 jobs/minute (with 30s timeout)

### Bottlenecks

1. **Container Creation**: 200-500ms per spawn (unavoidable for security)
2. **Filesystem Operations**: 50-100ms for mount setup
3. **Python Startup**: 100-200ms for interpreter initialization
4. **Sequential Processing**: Only one job runs at a time in POC

## Code & Library Constraints

### Python Version
- **Fixed at**: Python 3.11 (defined in Dockerfile)
- **Cannot use**: Python 3.12+ features
- **Workaround**: Update Dockerfile base image (requires rebuild)

### Custom Libraries
- **Must be pre-installed**: Cannot install at runtime
- **Format**: Wheel files (.whl) only
- **Location**: Place in `custom_libs/` before building image
- **Update process**: Rebuild Docker image and restart API

### Imports
- **All standard library**: Currently allowed (no whitelist)
- **Potentially dangerous modules**: Accessible but contained (os, subprocess, etc.)
- **Future enhancement**: Whitelist safe modules, block dangerous ones

## API Constraints

### Request/Response Format
- **JSON only**: Cannot upload binary files directly
- **Binary data**: Must be Base64 encoded (increases size ~33%)
- **Synchronous**: Client blocks until execution completes
- **No streaming**: Cannot see progress in real-time

### No Built-in Features (POC)
- No authentication or authorization
- No rate limiting
- No request queuing
- No result caching
- No webhook callbacks
- No long polling

## Known Issues (POC)

### 1. Cleanup Race Condition
- **Issue**: Worker crash during execution may leak temp directories
- **Impact**: Disk space gradually consumed
- **Workaround**: Periodic cleanup of `/tmp/job-*` directories
- **Planned fix**: Signal handlers for cleanup on crash

### 2. Large Output Handling
- **Issue**: Very large output JSON could block or timeout
- **Impact**: Jobs with >10MB output may fail
- **Workaround**: Keep output size reasonable (<1MB)
- **Planned fix**: Stream output, chunked reading

### 3. Error Message Exposure
- **Issue**: Docker errors may leak path information
- **Impact**: Could reveal server filesystem structure
- **Workaround**: Sanitize error messages in production
- **Planned fix**: Error message filtering

## Scalability Limits

### Single Server Limits (POC)

| Metric | Limit |
|--------|-------|
| Concurrent jobs | 1 (sequential) |
| Jobs per hour | ~120 (at 30s timeout) |
| API requests/sec | ~1-2 (with queuing) |

### Scaling Strategies (Future)

- **Horizontal**: Multiple API servers behind load balancer
- **Vertical**: Larger VM for more concurrent containers
- **Distributed**: Job queue with worker pool

## Future Roadmap (Not in POC)

Priority features for production:

1. **Scoped S3 Access** - Temporary credentials for file operations
2. **Async Job Queue** - Non-blocking API with job status polling
3. **Authentication** - API keys or JWT tokens
4. **Rate Limiting** - Per-client request limits
5. **Worker Pool** - Concurrent execution
6. **gVisor Runtime** - Stronger isolation
7. **Job History** - Persistent storage of results
8. **Import Whitelist** - Block dangerous Python modules
9. **Monitoring** - Metrics and alerting
10. **Stateful Execution** - Pass state between jobs

## Working Around Limitations

### For Network Access Needs
**Problem**: Code needs to fetch data from external API  
**Workaround**: Client fetches data, passes via `input_data`

### For Large Datasets
**Problem**: Input data >1MB  
**Workaround**: Pass reference/ID, use future S3 integration for actual data

### For Long-Running Jobs
**Problem**: Computation takes >5 minutes  
**Workaround**: Break into smaller jobs with checkpointing

### For Database Access
**Problem**: Code needs to query database  
**Workaround**: Client queries DB, passes data via `input_data`

### For Custom Packages
**Problem**: Need numpy, pandas, etc.  
**Workaround**: Add to `custom_libs/`, rebuild Docker image

## Getting Help

- See `README.md` for setup and usage
- Check GitHub issues for known bugs
- File new issue with "limitation" label for feature requests
- Review security documentation before production use

---

**Last updated**: 2024-01-24  
**POC Version**: 1.0.0
