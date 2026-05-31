import json
import os
import resource
import runpy
import sys


def apply_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
    memory_bytes = 128 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: hy_runner.py BOT_FILE")

    apply_limits()

    import hy  # noqa: F401

    event = json.loads(os.environ.get("MEYASUBAKO_EVENT", "{}"))

    def respond(value):
        print(json.dumps(value, ensure_ascii=False))

    globals_dict = {
        "__name__": "__meyasubako_bot__",
        "event": event,
        "respond": respond,
    }
    runpy.run_path(sys.argv[1], init_globals=globals_dict)


if __name__ == "__main__":
    main()
