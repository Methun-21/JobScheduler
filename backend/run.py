import os
import sys
import uvicorn

# Get the absolute path of the workspace root (d:\Mine\coditity)
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(backend_dir)

# Add it to sys.path so the parent process can resolve modules
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

# Set PYTHONPATH environment variable so child reload processes inherit it and can import 'backend'
os.environ["PYTHONPATH"] = project_dir + os.pathsep + os.environ.get("PYTHONPATH", "")

if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
