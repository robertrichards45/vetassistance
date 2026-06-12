import sys, compileall, subprocess, os

def main():
    ok = compileall.compile_dir("app", quiet=1)
    if not ok:
        print("❌ compileall failed")
        return 1
    print("✅ compileall passed")

    if os.path.exists("healthcheck.py"):
        r = subprocess.run([sys.executable, "healthcheck.py"], capture_output=True, text=True)
        print(r.stdout)
        if r.returncode != 0:
            print(r.stderr)
            print("❌ healthcheck failed")
            return r.returncode
        print("✅ healthcheck passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
