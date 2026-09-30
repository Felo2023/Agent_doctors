"""Run a SYNTH-001 mail round-trip with two isolated OpenCode TUI processes.

Only copies synthetic agent definitions/tools. Never opens clinical source files.
Requires `--demo-shared-auth` until isolated profile authentication is configured.
"""

import argparse
import base64
import json
import os
import pathlib
import pty
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import urllib.error
import urllib.request
import fcntl
import struct


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from runtime.models import resolve_model, opencode_configuration

ROLES = {"doctor-a": resolve_model("opus"), "doctor-b": resolve_model("sol")}


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(url, path, *, token=None, password=None, body=None, method="GET", timeout=10):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if password:
        headers["Authorization"] = "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()
    request = urllib.request.Request(url + path, headers=headers, method=method, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read()
        return response.status, json.loads(content) if content else None


def wait_health(url, password, process, seconds=30):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError("OpenCode exited before health check")
        try:
            _, data = call(url, "/global/health", password=password)
            if data["healthy"]:
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError("OpenCode health check timed out")


def stop(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def start_tui(role, url_port, sid, cwd, env):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    process = subprocess.Popen(["opencode", "--hostname", "127.0.0.1", "--port", str(url_port), "--agent", role, "--session", sid], cwd=cwd, env={**env, "TERM": "xterm-256color"}, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    stopped = threading.Event()

    def drain():
        while not stopped.is_set():
            try:
                if select.select([master], [], [], 0.3)[0]:
                    os.read(master, 65536)  # avoid a blocked TUI PTY
            except OSError:
                break

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    return process, master, stopped, reader


def inspect_doctor(role, doctors, bus_url, admin_token):
    doctor = doctors[role]
    _, pause = call(bus_url, "/pause", token=admin_token, method="POST", body={"role": role, "paused": True})
    if not pause["safe_for_human_turn"]:
        print("Waiting for the current letter to finish before opening this session...", flush=True)
    until = time.monotonic() + 180
    while time.monotonic() < until:
        _, mail = call(bus_url, "/mail", token=admin_token)
        in_flight = any(m["recipient"] == role and m["state"] in ("dispatched", "processing") for m in mail["messages"])
        _, status = call(doctor["url"], "/session/status", password=doctor["password"])
        active = status.get(doctor["session_id"], {}).get("type") not in (None, "idle")
        if not in_flight and not active:
            break
        time.sleep(0.4)
    else:
        print("Still busy; doctor remains paused. Inspect mail state before retrying.", flush=True)
        return
    print(f"Attaching to {role}; exit OpenCode to return to the supervisor.", flush=True)
    env = {**os.environ, "OPENCODE_SERVER_PASSWORD": doctor["password"]}
    try:
        subprocess.run(["opencode", "attach", doctor["url"], "--session", doctor["session_id"]],
                       env=env, check=True)
    finally:
        call(bus_url, "/pause", token=admin_token, method="POST", body={"role": role, "paused": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-shared-auth", action="store_true", help="Explicitly symlink existing user credential store in synthetic-only, private temp profiles")
    parser.add_argument("--hold", action="store_true", help="Interactive supervisor: inspect a paused doctor's TUI; quit stops processes")
    parser.add_argument("--restart-bus", action="store_true", help="Restart the bus after its first in-flight dispatch to check saved-turn recovery")
    parser.add_argument("--case-root", help="Optional marked synthetic fixture with a committed reviewed version")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.hold and not sys.stdin.isatty():
        parser.error("--hold requires an interactive terminal")
    if not args.demo_shared_auth:
        parser.error("isolated profiles have no credentials; use --demo-shared-auth for SYNTH-001 only (or configure separate profile logins)")
    auth = pathlib.Path.home() / ".local/share/opencode/auth.json"
    if not auth.is_file():
        parser.error("existing OpenCode credentials not found; sign in with opencode providers first")
    case_id = "SYNTH-001"
    case_version = None
    if args.case_root:
        from case_review.store import ReviewStore
        checked = ReviewStore(args.case_root)
        try:
            checked.verify_snapshot()
            case_id = checked.case_id
            case_version = checked.version()["version"]
        finally:
            checked.close()
    private = pathlib.Path(tempfile.mkdtemp(prefix="synthetic-mail-"))
    private.chmod(0o700)
    token = secrets.token_urlsafe(40)
    role_tokens = {role: secrets.token_urlsafe(40) for role in ROLES}
    passwords = {role: secrets.token_urlsafe(24) for role in ROLES}
    bus_port = unused_port()
    bus_url = f"http://127.0.0.1:{bus_port}"
    processes = []
    tuis = []
    doctors = {}
    try:
        bus_env = {**os.environ, "PYTHONPATH": str(ROOT), "PROTOTYPE_BUS_TOKEN": token,
                   "PROTOTYPE_SERVER_PASSWORD_A": passwords["doctor-a"],
                   "PROTOTYPE_SERVER_PASSWORD_B": passwords["doctor-b"]}
        bus_log = (private / "bus.log").open("w")
        bus_args = [sys.executable, "-m", "bus.prototype", "--db", str(private / "mail.sqlite"), "--port", str(bus_port), "--case-id", case_id]
        if args.case_root:
            bus_args.extend(["--case-root", str(pathlib.Path(args.case_root).resolve())])
        bus = subprocess.Popen(bus_args, cwd=ROOT, env=bus_env, stdout=bus_log, stderr=subprocess.STDOUT)
        processes.append(bus)
        for _ in range(100):
            try:
                call(bus_url, "/health", token=token)
                break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mail bus did not start")
        for role, model in ROLES.items():
            password = passwords[role]
            location = private / role
            work = location / "workspace"
            agent_dir = work / ".opencode/agents"
            tool_dir = work / ".opencode/tools"
            agent_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            shutil.copy2(ROOT / ".opencode/agents" / (role + ".md"), agent_dir / (role + ".md"))
            shutil.copy2(ROOT / ".opencode/tools/prototype_mail.ts", tool_dir / "prototype_mail.ts")
            if args.case_root:
                shutil.copy2(ROOT / ".opencode/tools/prototype_case_read.ts", tool_dir / "prototype_case_read.ts")
            data_dir = location / "data/opencode"
            data_dir.mkdir(parents=True)
            (data_dir / "auth.json").symlink_to(auth)
            port = unused_port()
            url = f"http://127.0.0.1:{port}"
            env = {**os.environ, "XDG_DATA_HOME": str(location / "data"), "XDG_CONFIG_HOME": str(location / "config"), "XDG_CACHE_HOME": str(location / "cache"), "OPENCODE_SERVER_PASSWORD": password, "OPENCODE_CONFIG_CONTENT": json.dumps(opencode_configuration(model)), "OPENCODE_DISABLE_AUTOUPDATE": "1", "PROTOTYPE_BUS_URL": bus_url, "PROTOTYPE_ROLE_TOKEN": role_tokens[role], "PROTOTYPE_CASE_ID": case_id}
            if case_version is not None:
                env["PROTOTYPE_CASE_VERSION"] = str(case_version)
            # Bootstrap session via a short-lived server, then resume in an actual TUI.
            with (location / "bootstrap.log").open("w") as log:
                bootstrap = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(port)], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_health(url, password, bootstrap)
                    _, session = call(url, "/session", password=password, method="POST", body={"title": case_id + " / " + role})
                    sid = session["id"]
                finally:
                    stop(bootstrap)
            process, master, stopped, reader = start_tui(role, port, sid, work, env)
            processes.append(process)
            tuis.append((master, stopped, reader))
            wait_health(url, password, process)
            call(bus_url, "/register", token=token, method="POST", body={"role": role, "url": url, "session_id": sid, "model": model, "role_token": role_tokens[role]})
            doctors[role] = {"url": url, "session_id": sid, "model": model, "password": password}
        manifest = {role: {key: value for key, value in row.items() if key != "password"} for role, row in doctors.items()}
        (private / "sessions.json").write_text(json.dumps(manifest, indent=2))
        print("Synthetic workspace:", private, flush=True)
        print("Two independent TUIs started. Attach URLs and session IDs are in private sessions.json (server password remains undisclosed).", flush=True)
        call(bus_url, "/phase", token=token, method="POST", body={"phase": "discussion"})
        first = doctors["doctor-a"]
        password = first["password"]
        provider, model_id = first["model"].split("/", 1)
        question = ("Synthetic-only. First call prototype_case_read. Send doctor-b a short question asking it to independently read the same reviewed version and reply with one source block ID and the source's date or 'unknown'. No diagnosis, treatment or patient data. Stop after sending." if args.case_root else "SYNTH-001 only. Use prototype_mail to send doctor-b this exact question: What is 7 + 5? Request a reply, then stop.")
        prompt = {"agent": "doctor-a", "model": {"providerID": provider, "modelID": model_id}, "parts": [{"type": "text", "text": question}]}
        status, _ = call(first["url"], f"/session/{first['session_id']}/prompt_async", password=password, method="POST", body=prompt)
        if status != 204:
            raise RuntimeError("seed prompt not accepted")
        until = time.monotonic() + args.timeout
        restarted_bus = False
        while time.monotonic() < until:
            _, state = call(bus_url, "/mail", token=token)
            rows = state["messages"]
            if args.restart_bus and not restarted_bus and any(r["state"] in ("dispatched", "processing") for r in rows):
                stop(bus)
                bus = subprocess.Popen(bus_args, cwd=ROOT, env=bus_env, stdout=bus_log, stderr=subprocess.STDOUT)
                processes[0] = bus  # bus is always the first supervised process
                for _ in range(100):
                    try:
                        call(bus_url, "/health", token=token)
                        break
                    except urllib.error.URLError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("mail bus did not restart")
                restarted_bus = True
                print("Bus restarted with an in-flight letter", flush=True)
            if len(rows) >= 2 and all(r["state"] == "completed" for r in rows[:2]):
                print("PASS: two letters delivered and completed:", [(r["sender"], r["recipient"], r["state"]) for r in rows[:2]], flush=True)
                break
            if any(r["state"] in ("failed", "uncertain") for r in rows):
                raise RuntimeError("mail delivery incomplete: " + str([(r["id"], r["state"], r["error"]) for r in rows]))
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("a process exited during delivery")
            time.sleep(0.5)
        else:
            raise TimeoutError("two-doctor round trip did not complete; check private mail.sqlite and session history")
        for role, doctor in doctors.items():
            _, messages = call(doctor["url"], f"/session/{doctor['session_id']}/message", password=doctor["password"])
            (private / (role + "-messages.json")).write_text(json.dumps(messages, indent=2))
        if args.hold:
            print("Supervisor commands: inspect doctor-a | inspect doctor-b | status | quit", flush=True)
            while True:
                command = input("synthetic> ").strip()
                if command == "quit":
                    break
                if command == "status":
                    _, mail = call(bus_url, "/mail", token=token)
                    print([(m["id"], m["recipient"], m["state"]) for m in mail["messages"]], flush=True)
                elif command in ("inspect doctor-a", "inspect doctor-b"):
                    inspect_doctor(command.split()[1], doctors, bus_url, token)
                else:
                    print("Unknown command", flush=True)
        return 0
    finally:
        for process in reversed(processes):
            stop(process)
        # Retain synthetic sessions/transcripts, but remove credential links.
        for role in ROLES:
            link = private / role / "data/opencode/auth.json"
            if link.is_symlink():
                link.unlink()
        for master, stopped, reader in tuis:
            stopped.set()
            reader.join(timeout=2)
            os.close(master)
        if "bus_log" in locals():
            bus_log.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Stopped synthetic demo", file=sys.stderr)
        sys.exit(130)
