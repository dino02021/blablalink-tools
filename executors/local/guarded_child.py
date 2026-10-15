"""Internal trusted Python-module bootstrap; not a sandbox for untrusted code."""

import sys


def main():
    memory_bytes, output_bytes = int(sys.argv[1]), int(sys.argv[2])
    module, arguments = sys.argv[3], sys.argv[4:]
    if sys.stdin.buffer.readline(32) != b"start\n":
        return 2
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (output_bytes, output_bytes))
    elif sys.platform != "win32":
        return 2
    import runpy
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
