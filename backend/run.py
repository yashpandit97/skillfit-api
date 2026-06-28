"""
Run uvicorn from project root so that 'backend' is importable.
Usage: from backend/ run:  python run.py
        from project root: uvicorn backend.main:app --reload --port 8000
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
<<<<<<< HEAD
        port=8000,
=======
        port=8001,
>>>>>>> 1aa7648 (deployment changes + bug fixes)
        reload=True,
        reload_dirs=[str(root)],
    )
