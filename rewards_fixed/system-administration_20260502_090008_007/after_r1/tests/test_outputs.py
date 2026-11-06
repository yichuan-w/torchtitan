import configparser
import json
import subprocess
import time
import urllib.request

PROGRAMS = ("appdb", "appcache", "appserver", "appworker", "appproxy")
CONF_DIR = "/etc/supervisor/conf.d"


def command(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def supervisor_status():
    result = command(["supervisorctl", "status"], timeout=10)
    states = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            states[fields[0]] = fields[1]
    return result, states


def load_programs():
    programs = {}
    for path in __import__("glob").glob(f"{CONF_DIR}/*.conf"):
        parser = configparser.RawConfigParser(strict=False)
        parser.read(path)
        for section in parser.sections():
            if section.startswith("program:"):
                programs[section.split(":", 1)[1]] = dict(parser.items(section))
    return programs


def priority(programs, name):
    try:
        return int(programs[name].get("priority", "999"))
    except ValueError:
        return 999


def test_stack_contract():
    programs = load_programs()
    init_programs = [
        name for name, settings in programs.items()
        if "/opt/webapp/init_db.py" in settings.get("command", "")
    ]
    assert (
        all(name in programs for name in PROGRAMS)
        and len(init_programs) == 1
        and priority(programs, "appdb") < priority(programs, init_programs[0])
        and priority(programs, "appcache") < priority(programs, "appserver")
        and priority(programs, "appcache") < priority(programs, "appworker")
        and priority(programs, init_programs[0]) < priority(programs, "appserver")
        and priority(programs, init_programs[0]) < priority(programs, "appworker")
        and priority(programs, "appserver") < priority(programs, "appproxy")
    ), "Supervisor programs do not encode the required startup order and migration"

    status_result, states = supervisor_status()
    assert (
        status_result.returncode in (0, 3)
        and all(states.get(name) == "RUNNING" for name in PROGRAMS)
    ), f"Required Supervisor services are not all RUNNING: {status_result.stdout} {status_result.stderr}"

    database = command([
        "psql", "postgresql://appuser:apppass123@localhost/appdb", "-Atqc",
        "SELECT to_regclass('public.items') IS NOT NULL AND to_regclass('public.task_queue') IS NOT NULL",
    ], timeout=15)
    assert database.returncode == 0 and database.stdout.strip() == "t", (
        f"Migration schema is absent: {database.stdout} {database.stderr}"
    )

    response = None
    error = None
    for _ in range(15):
        try:
            with urllib.request.urlopen("http://localhost/health", timeout=3) as request:
                response = (request.status, json.load(request))
            break
        except Exception as exc:
            error = exc
            time.sleep(1)
    assert response == (200, {"cache": "connected", "database": "connected"}), (
        f"Port 80 health endpoint is not fully connected: {response!r}; last error: {error!r}"
    )


if __name__ == "__main__":
    test_stack_contract()
    print("stack contract satisfied")
