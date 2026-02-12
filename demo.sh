#!/usr/bin/env zsh
# Tako VM Comprehensive Demo Script
# Run: ./demo.sh [--quick] [--skip-setup]
# Options:
#   --quick      Skip pauses between sections
#   --skip-setup Skip installation and server startup (assumes server running)

set -e

# Colors for output
autoload -U colors && colors
info() { print -P "%F{blue}[INFO]%f $1" }
success() { print -P "%F{green}[OK]%f $1" }
warn() { print -P "%F{yellow}[WARN]%f $1" }
error() { print -P "%F{red}[ERROR]%f $1" }
header() {
    print -P "\n%F{cyan}═══════════════════════════════════════════════════════════%f"
    print -P "%F{cyan}  $1%f"
    print -P "%F{cyan}═══════════════════════════════════════════════════════════%f\n"
}

# Parse arguments
QUICK=false
SKIP_SETUP=false
for arg in "$@"; do
    case $arg in
        --quick) QUICK=true ;;
        --skip-setup) SKIP_SETUP=true ;;
    esac
done

pause() {
    if [[ "$QUICK" != "true" ]]; then
        print -P "\n%F{yellow}Press Enter to continue...%f"
        read -r
    fi
}

show_json() {
    python3 -m json.tool 2>/dev/null || cat
}

show_code() {
    print -P "%F{magenta}┌─ Code ─────────────────────────────────────────────────────%f"
    printf '%s\n' "$1" | while IFS= read -r line; do
        print -P "%F{magenta}│%f $line"
    done
    print -P "%F{magenta}└────────────────────────────────────────────────────────────%f"
}

DEMO_DIR="${0:A:h}"
cd "$DEMO_DIR"

BASE_URL="http://localhost:8000"
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        info "Stopping server..."
        kill $SERVER_PID 2>/dev/null || true
        wait $SERVER_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ============================================================================
if [[ "$SKIP_SETUP" != "true" ]]; then
# ============================================================================
header "SETUP - Install Tako VM"
# ============================================================================

# Check for uv
if ! command -v uv &> /dev/null; then
    error "uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Check for Docker
if ! command -v docker &> /dev/null; then
    error "Docker not found. Please install Docker first."
    exit 1
fi

info "Creating virtual environment with uv..."
uv venv --quiet .venv 2>/dev/null || true
source .venv/bin/activate

info "Installing tako-vm..."
uv pip install -e ".[server]" --quiet

success "Tako VM installed"
tako-vm version

# ============================================================================
header "BUILD BASE IMAGE"
# ============================================================================

info "Building code-executor base image..."
if [[ -f "docker/Dockerfile.executor" ]]; then
    docker build -t code-executor:latest -f docker/Dockerfile.executor . --quiet
    success "Base image built"
else
    warn "Dockerfile not found, skipping image build"
fi

# ============================================================================
header "START SERVER"
# ============================================================================

# Check if Tako VM is already running on port 8000
HEALTH_RESPONSE=$(curl -s $BASE_URL/health 2>/dev/null || echo "")
if echo "$HEALTH_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if 'docker_available' in d else 1)" 2>/dev/null; then
    info "Tako VM server already running at $BASE_URL, using existing server"
    # Don't set SERVER_PID so we don't kill it on exit
else
    info "Starting Tako VM server..."
    cp tako_vm.yaml.example tako_vm.yaml 2>/dev/null || true

    tako-vm server --port 8000 &
    SERVER_PID=$!
    sleep 3
fi

fi # end SKIP_SETUP

# Check server is Tako VM (not just any service on port 8000)
HEALTH_RESPONSE=$(curl -s $BASE_URL/health 2>/dev/null || echo "")
if ! echo "$HEALTH_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if 'docker_available' in d else 1)" 2>/dev/null; then
    if [[ -n "$HEALTH_RESPONSE" ]]; then
        error "Port 8000 is in use by another service (not Tako VM)"
        error "Stop the other service or use a different port"
    else
        error "Server not running at $BASE_URL"
        error "Start with: tako-vm server --port 8000"
    fi
    exit 1
fi
success "Tako VM server running at $BASE_URL"

# Check gVisor status and show note if running in permissive mode
GVISOR_AVAILABLE=$(curl -s $BASE_URL/health | python3 -c "import json,sys; print(json.load(sys.stdin).get('gvisor_available', False))" 2>/dev/null || echo "False")
if [[ "$GVISOR_AVAILABLE" != "True" ]]; then
    print -P "\n%F{yellow}┌────────────────────────────────────────────────────────────┐%f"
    print -P "%F{yellow}│%f  %F{white}NOTE: Running without gVisor (permissive mode)%f             %F{yellow}│%f"
    print -P "%F{yellow}│%f  The warnings above are expected for local development.    %F{yellow}│%f"
    print -P "%F{yellow}│%f  In production, install gVisor for full container isolation.%F{yellow}│%f"
    print -P "%F{yellow}└────────────────────────────────────────────────────────────┘%f\n"
fi

# ============================================================================
header "1. HEALTH CHECK - System Status"
# ============================================================================

info "Checking system health..."
curl -s $BASE_URL/health | show_json

print -P "\n%F{green}Shows:%f Docker status, gVisor availability, circuit breaker state"
pause

# ============================================================================
header "2. SIMPLE EXECUTION - Hello World"
# ============================================================================

CODE='print(2 + 2)'
show_code "$CODE"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "print(2 + 2)"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
print(f\"Output: {data.get('stdout', '').strip()}\")"

print -P "\n%F{green}✓%f Code runs in isolated Docker container, stdout captured"
pause

# ============================================================================
header "3. INPUT/OUTPUT - Pass Data In, Get Results Out"
# ============================================================================

CODE='import json
with open("/input/data.json") as f:
    data = json.load(f)
result = {"sum": data["a"] + data["b"], "product": data["a"] * data["b"]}
with open("/output/result.json", "w") as f:
    json.dump(result, f)
print(f"Calculated: {result}")'
show_code "$CODE"
print -P "%F{yellow}Input data:%f {\"a\": 10, \"b\": 20}"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{
        "code": "import json\nwith open(\"/input/data.json\") as f:\n    data = json.load(f)\nresult = {\"sum\": data[\"a\"] + data[\"b\"], \"product\": data[\"a\"] * data[\"b\"]}\nwith open(\"/output/result.json\", \"w\") as f:\n    json.dump(result, f)\nprint(f\"Calculated: {result}\")",
        "input_data": {"a": 10, "b": 20}
    }')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
print(f\"stdout: {data.get('stdout', '').strip()}\")
print(f\"output: {json.dumps(data.get('output', {}), indent=2)}\")"

print -P "\n%F{green}Key points:%f"
print "  • input_data available at /input/data.json"
print "  • Write JSON to /output/result.json → appears in 'output' field"
pause

# ============================================================================
header "4. JOB TYPES - Pre-configured Environments"
# ============================================================================

info "Available job types:"
curl -s $BASE_URL/job-types | python3 -c "
import json, sys
data = json.load(sys.stdin)
for jt in data:
    reqs = ', '.join(jt.get('requirements', [])) or 'stdlib only'
    print(f\"  • {jt['name']}: {reqs} (mem={jt['memory_limit']}, timeout={jt['timeout']}s)\")"

pause

CODE='import pandas as pd
import numpy as np
df = pd.DataFrame({"x": np.random.randn(5)})
print(df.describe())'
show_code "$CODE"
print -P "%F{yellow}Job type:%f data-processing (includes pandas, numpy)"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{
        "job_type": "data-processing",
        "code": "import pandas as pd\nimport numpy as np\ndf = pd.DataFrame({\"x\": np.random.randn(5)})\nprint(df.describe())"
    }')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=120" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
print(f\"\\nOutput:\\n{data.get('stdout', '')}\")
if 'timing' in data and data['timing']:
    t = data['timing']
    print(f\"\\nTiming:  startup={t.get('startup_ms', 0)}ms  deps={t.get('dep_install_ms', 0)}ms  exec={t.get('execution_ms', 0)}ms\")"

print -P "\n%F{green}✓%f Dependencies installed automatically via uv (cached for speed)"
pause

# ============================================================================
header "5. ASYNC EXECUTION - Long Running Jobs"
# ============================================================================

CODE='import time
for i in range(3):
    print(f"Step {i+1}/3...")
    time.sleep(1)
print("Done!")'
show_code "$CODE"
info "Submitting async job..."
ASYNC_RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "import time\nfor i in range(3):\n    print(f\"Step {i+1}/3...\")\n    time.sleep(1)\nprint(\"Done!\")"}')

JOB_ID=$(echo "$ASYNC_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
print "Job ID: $JOB_ID"

info "Checking job status..."
curl -s $BASE_URL/jobs/$JOB_ID | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")"

info "Waiting for result (blocking call)..."
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=10" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
print(f\"Output:\\n{data.get('stdout', '')}\")"

pause

# ============================================================================
header "6. WORKER POOL STATS"
# ============================================================================

info "Pool statistics:"
curl -s $BASE_URL/pool/stats | show_json

print -P "\n%F{green}Shows:%f pending jobs, running jobs, worker count"
pause

# ============================================================================
header "7. SECURITY - Network Isolation"
# ============================================================================

CODE='import socket
socket.create_connection(("8.8.8.8", 53), timeout=2)'
show_code "$CODE"
info "Attempting network access (should fail)..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "import socket; socket.create_connection((\"8.8.8.8\", 53), timeout=2)"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
if data.get('error'):
    print(f\"Error: {data['error'].get('type', 'unknown')}\")"

print -P "\n%F{green}✓ Network blocked%f - code cannot phone home or exfiltrate data"
pause

# ============================================================================
header "8. SECURITY - Filesystem Isolation"
# ============================================================================

CODE='open("/etc/passwd", "w").write("hacked")'
show_code "$CODE"
info "Attempting to write to system files..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "open(\"/etc/passwd\", \"w\").write(\"hacked\")"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
if data.get('error'):
    print(f\"Error type: {data['error'].get('type', 'unknown')}\")"

print -P "\n%F{green}✓ Filesystem read-only%f - system files protected"
pause

# ============================================================================
header "9. SECURITY - Resource Limits (OOM)"
# ============================================================================

CODE='x = bytearray(500 * 1024 * 1024)  # 500 MB'
show_code "$CODE"
info "Attempting to allocate 500MB (exceeds limit)..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "x = bytearray(500 * 1024 * 1024)", "timeout": 10}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
if data.get('error'):
    print(f\"Error type: {data['error'].get('type', 'unknown')}\")"

print -P "\n%F{green}✓ OOM killed%f - memory limits enforced per job"
pause

# ============================================================================
header "10. SECURITY - Timeout Protection"
# ============================================================================

CODE='while True: pass  # infinite loop'
show_code "$CODE"
print -P "%F{yellow}Timeout:%f 2 seconds"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "while True: pass", "timeout": 2}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data['status']}\")
if data.get('error'):
    print(f\"Error type: {data['error'].get('type', 'unknown')}\")"

print -P "\n%F{green}✓ Timeout enforced%f - runaway code terminated"
pause

# ============================================================================
header "11. TIMING BREAKDOWN - Performance Visibility"
# ============================================================================

CODE='import pandas
print(f"pandas {pandas.__version__}")'
show_code "$CODE"
print -P "%F{yellow}Job type:%f data-processing"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"job_type": "data-processing", "code": "import pandas; print(f\"pandas {pandas.__version__}\")"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=120" | python3 -c "
import json, sys
data = json.load(sys.stdin)
t = data.get('timing') or {}
print('Timing breakdown:')
print(f\"  startup_ms:     {t.get('startup_ms', 'N/A'):>6}  (container init)\")
print(f\"  dep_install_ms: {t.get('dep_install_ms', 'N/A'):>6}  (uv installing packages)\")
print(f\"  execution_ms:   {t.get('execution_ms', 'N/A'):>6}  (your code running)\")
print(f\"  total_ms:       {t.get('total_ms', 'N/A'):>6}  (end-to-end)\")"

print -P "\n%F{green}Use case:%f Identify bottlenecks - slow deps vs slow code"
pause

# ============================================================================
header "12. ARTIFACTS - File Output"
# ============================================================================

CODE='with open("/output/report.txt", "w") as f:
    f.write("Tako VM Report\\n")
    f.write("=" * 40 + "\\n")
    f.write("All systems operational\\n")

with open("/output/data.csv", "w") as f:
    f.write("id,value\\n1,100\\n2,200\\n3,300\\n")

print("Generated 2 artifacts")'
show_code "$CODE"
info "Executing..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{
        "code": "with open(\"/output/report.txt\", \"w\") as f:\n    f.write(\"Tako VM Report\\n\")\n    f.write(\"=\" * 40 + \"\\n\")\n    f.write(\"All systems operational\\n\")\n\nwith open(\"/output/data.csv\", \"w\") as f:\n    f.write(\"id,value\\n1,100\\n2,200\\n3,300\\n\")\n\nprint(\"Generated 2 artifacts\")"
    }')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")

curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" > /dev/null

info "Listing artifacts (using ?view=full):"
curl -s "$BASE_URL/jobs/$JOB_ID/result?view=full" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for a in data.get('artifacts', []):
    print(f\"  • {a['name']} ({a.get('size_bytes', 'N/A')} bytes)\")"

info "Downloading report.txt:"
curl -s "$BASE_URL/jobs/$JOB_ID/artifacts/report.txt"

print -P "\n%F{green}Use case:%f Generate reports, CSV exports, images, etc."
pause

# ============================================================================
header "13. RERUN & FORK - Time Machine Debugging"
# ============================================================================

CODE='import json
with open("/input/data.json") as f:
    data = json.load(f)
print(f"Processing: {data}")
print("Version 1")'
show_code "$CODE"
print -P "%F{yellow}Input data:%f {\"items\": [1, 2, 3]}"
info "Creating original job..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "import json\nwith open(\"/input/data.json\") as f:\n    data = json.load(f)\nprint(f\"Processing: {data}\")\nprint(\"Version 1\")", "input_data": {"items": [1, 2, 3]}}')
ORIGINAL_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$ORIGINAL_ID/result?wait=true&timeout=30" > /dev/null
print "Original job: $ORIGINAL_ID"

info "Rerun - exact same code and inputs:"
RERUN=$(curl -s -X POST "$BASE_URL/jobs/$ORIGINAL_ID/rerun" \
    -H "Content-Type: application/json" \
    -d '{}')
echo "$RERUN" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"New job ID: {data['job_id']}\")"

info "Fork - new code, same inputs:"
FORK_CODE='import json
with open("/input/data.json") as f:
    data = json.load(f)
print(f"Processing: {data}")
print("Version 2 - IMPROVED!")'
show_code "$FORK_CODE"
FORK=$(curl -s -X POST "$BASE_URL/jobs/$ORIGINAL_ID/fork" \
    -H "Content-Type: application/json" \
    -d '{"code": "import json\nwith open(\"/input/data.json\") as f:\n    data = json.load(f)\nprint(f\"Processing: {data}\")\nprint(\"Version 2 - IMPROVED!\")"}')
echo "$FORK" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"New job ID: {data['job_id']}\")"

print -P "\n%F{green}Use case:%f Debug failures, iterate on code without re-uploading data"
pause

# ============================================================================
header "14. IDEMPOTENCY - Safe Retries"
# ============================================================================

CODE='print(42)'
show_code "$CODE"

IDEM_KEY="demo-$(date +%s)"

info "First request with idempotency_key=$IDEM_KEY..."
FIRST_JOB_ID=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "print(42)", "idempotency_key": "'"$IDEM_KEY"'"}' \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
print "First job ID: $FIRST_JOB_ID"

# Wait for first job to complete
curl -s "$BASE_URL/jobs/$FIRST_JOB_ID/result?wait=true&timeout=30" > /dev/null

info "Second request with SAME key (should return cached job ID)..."
SECOND_JOB_ID=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "print(42)", "idempotency_key": "'"$IDEM_KEY"'"}' \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
print "Second job ID: $SECOND_JOB_ID"

if [[ "$FIRST_JOB_ID" == "$SECOND_JOB_ID" ]]; then
    print -P "\n%F{green}✓ Same job ID returned%f - idempotency working!"
else
    print -P "\n%F{yellow}⚠ Different job IDs%f - check idempotency implementation"
fi

print -P "\n%F{green}Use case:%f Safe retries in distributed systems, at-most-once execution"
pause

# ============================================================================
header "15. EXECUTION HISTORY"
# ============================================================================

info "Recent executions:"
curl -s "$BASE_URL/executions?limit=5" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for e in data.get('items', []):
    print(f\"  {e['execution_id'][:8]}... | {e['status']:10} | {e.get('job_type', 'default'):15} | {e['created_at']}\")"

print -P "\n%F{green}Use case:%f Audit trail, debugging, compliance"
pause

# ============================================================================
header "16. ERROR CLASSIFICATION"
# ============================================================================

info "Different errors are classified by type:"

print "\nSyntaxError:"
show_code 'def broken('
RESULT=$(curl -s -X POST $BASE_URL/execute/async -H "Content-Type: application/json" -d '{"code": "def broken("}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
err = data.get('error') or {}
print(f\"  Status: {data['status']}, Type: {err.get('type', 'N/A')}\")"

print "\nImportError:"
show_code 'import nonexistent_xyz_module'
RESULT=$(curl -s -X POST $BASE_URL/execute/async -H "Content-Type: application/json" -d '{"code": "import nonexistent_xyz_module"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
err = data.get('error') or {}
print(f\"  Status: {data['status']}, Type: {err.get('type', 'N/A')}\")"

print "\nZeroDivisionError:"
show_code 'x = 1/0'
RESULT=$(curl -s -X POST $BASE_URL/execute/async -H "Content-Type: application/json" -d '{"code": "x = 1/0"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
curl -s "$BASE_URL/jobs/$JOB_ID/result?wait=true&timeout=30" | python3 -c "
import json, sys
data = json.load(sys.stdin)
err = data.get('error') or {}
print(f\"  Status: {data['status']}, Type: {err.get('type', 'N/A')}\")"

print -P "\n%F{green}15+ error types%f for actionable debugging"
pause

# ============================================================================
header "17. DEAD LETTER QUEUE"
# ============================================================================

info "DLQ statistics:"
curl -s $BASE_URL/dlq/stats | show_json

print -P "\n%F{green}Use case:%f Failed async jobs preserved for debugging"
pause

# ============================================================================
header "18. JOB CANCELLATION"
# ============================================================================

CODE='import time
while True:
    time.sleep(1)
    print("still running...")'
show_code "$CODE"
info "Submitting a long job..."
RESULT=$(curl -s -X POST $BASE_URL/execute/async \
    -H "Content-Type: application/json" \
    -d '{"code": "import time\nwhile True:\n    time.sleep(1)\n    print(\"still running...\")"}')
JOB_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
print "Job ID: $JOB_ID"
sleep 2

info "Cancelling job..."
curl -s -X POST "$BASE_URL/jobs/$JOB_ID/cancel" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Status: {data.get('status', 'unknown')}, Job ID: {data.get('job_id', 'unknown')}\")"

print -P "\n%F{green}Use case:%f Stop runaway jobs, cleanup"
pause

# ============================================================================
header "19. CLI TOOLS"
# ============================================================================

info "tako-vm status:"
tako-vm status --url $BASE_URL 2>/dev/null || print "  (run: tako-vm status)"

info "Other CLI commands:"
print "  tako-vm server --port 8000     # Start server"
print "  tako-vm config --json          # Show configuration"
print "  tako-vm validate config.yaml   # Validate config file"
print "  tako-vm build job-type NAME    # Pre-build image"

pause

# ============================================================================
header "DEMO COMPLETE!"
# ============================================================================

print -P "
%F{green}Tako VM Capabilities Summary:%f

  %F{cyan}Execution%f
    ✓ Sync and async code execution
    ✓ Input/output data with JSON
    ✓ Job types with pre-configured dependencies
    ✓ File artifacts (upload/download)
    ✓ Phase-aware timing breakdown

  %F{cyan}Security%f
    ✓ gVisor isolation (user-space kernel)
    ✓ Network isolation (default: blocked)
    ✓ Read-only filesystem
    ✓ Memory/CPU limits per job
    ✓ Timeout enforcement
    ✓ Seccomp syscall filtering

  %F{cyan}Operations%f
    ✓ Worker pool with queue
    ✓ Rerun/Fork for debugging
    ✓ Idempotency for safe retries
    ✓ Execution history (30-day retention)
    ✓ Error classification (15+ types)
    ✓ Dead letter queue
    ✓ Job cancellation
    ✓ Health checks & circuit breaker

%F{cyan}Documentation:%f
  • README.md
  • docs/api/rest.md
  • tako_vm.yaml.example
"
