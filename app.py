import sys
if sys.version_info >= (3, 14):
    raise RuntimeError("Python 3.14 is not supported on Windows for this build (Pillow wheels unavailable). Install Python 3.12.x (recommended) or 3.13.x.")

import warnings
try:
    from cryptography.utils import CryptographyDeprecationWarning
    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except Exception:
    pass

"""Convenience entrypoint.

Run:
  python app.py

For production, use wsgi.py with a WSGI server.
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
