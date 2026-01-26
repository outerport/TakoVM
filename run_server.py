#!/usr/bin/env python3
"""Start the Tako VM API server."""

if __name__ == "__main__":
    import uvicorn
    from tako_vm.api_server import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
