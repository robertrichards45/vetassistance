from __future__ import annotations

import sys
import compileall

def main() -> int:
    print("Python:", sys.version)
    ok = compileall.compile_dir("app", quiet=1)
    if not ok:
        print("❌ compileall failed (syntax errors).")
        return 1
    try:
        from app import create_app
        app = create_app()
        print("✅ create_app() OK:", app)
    except Exception as e:
        print("❌ Import/create_app failed:", repr(e))
        raise
    print("✅ Healthcheck passed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
