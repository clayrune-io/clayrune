"""Disposable full-boot delivery evidence with all non-delivery producers fenced."""
from __future__ import annotations
import json, os, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
import pytest
from mc.delegation_delivery import DeliveryStore

ROOT = Path(__file__).parents[1]
CHILD = r'''
import json, os, sys, threading
import socket
from pathlib import Path
from types import ModuleType
from flask import jsonify, request
root = Path(sys.argv[1]); port = int(sys.argv[2])
launch_path = root / "fake-launches.jsonl"
handoff_path = root / "fake-handoffs.jsonl"
trace_path = root / "boot-trace.jsonl"
def record(path, value):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, sort_keys=True) + "\n")
# Fail closed if any un-stubbed startup path attempts a socket outside this
# child. Registration is performed by the parent, so the child needs exactly
# one permitted connect destination: its own ephemeral HTTP port.
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
def _own_port_only(sock, address):
    host, target_port = address[0], address[1]
    if host != "127.0.0.1" or target_port != port:
        raise RuntimeError(f"network blocked by startup harness: {host}:{target_port}")
    return _socket_connect(sock, address)
socket.socket.connect = _own_port_only
def _own_port_connect_ex(sock, address):
    host, target_port = address[0], address[1]
    if host != "127.0.0.1" or target_port != port:
        raise RuntimeError(f"network blocked by startup harness: {host}:{target_port}")
    return _socket_connect_ex(sock, address)
socket.socket.connect_ex = _own_port_connect_ex
def _blocked_sendto(*args, **kwargs):
    raise RuntimeError("datagram network blocked by startup harness")
socket.socket.sendto = _blocked_sendto
# No startup producer may launch a real CLI/process in this child. The fake
# runtime below is an object seam, not a subprocess replacement.
import subprocess as _subprocess
def _blocked_process(*args, **kwargs):
    raise RuntimeError("process launch blocked by startup harness")
_subprocess.Popen = _blocked_process
_subprocess.run = _blocked_process
_subprocess.call = _blocked_process
os.system = _blocked_process
# Prevent import-time remote/provider/keyring/mail effects.
for name in ("mc_remote_iface", "mc_remote", "mc_remote.config",
             "mc_remote.device_keys", "mc_remote.enrollment", "keyring", "mail"):
    sys.modules[name] = ModuleType(name)
import server
from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import drain_once
# Assert the values bound by the real import/wiring before invoking boot.
expected_root = root / "mc-data"
assert Path(server._DATA_ROOT).resolve() == expected_root.resolve()
assert Path(server.DATA_DIR).resolve() == (expected_root / "data" / "projects").resolve()
assert Path(ar._delivery_path).resolve() == (expected_root / "data" / "delegation_delivery.sqlite3").resolve()
assert Path(ar.PROVIDER_ENV_PATH).resolve() == (expected_root / "data" / "provider_env.json").resolve()
assert Path(server._PID_LEDGER_PATH).resolve() == (expected_root / "data" / "mc_child_pids.json").resolve()
assert Path.home().resolve() == (root / "userprofile").resolve()
server.PORT = port
ar.PORT = port
server.CONFIG["question_channel"] = "off"
server.CONFIG["keep_awake_enabled"] = False
ar.agent_sessions.clear()
trace, mode = [], {"value": "normal"}
lock_release, lock_held = threading.Event(), threading.Event()
shutdown_inbox_entered = threading.Event()
def traced(name):
    def call(*args, **kwargs):
        trace.append(name)
    return call
# Stub every startup producer except real delegation delivery.
server._bp_sched._start_scheduler = traced("scheduler")
server._start_hivemind_orchestrator = traced("hivemind")
server._bp_coord.start_coordination_loop = traced("coordination")
server._worktree_gc_on_startup = traced("worktree-gc")
server._start_session_guardian = traced("guardian")
server._install_builtin_skills = traced("install-skills")
server._install_builtin_mcps = traced("install-mcps")
server._bp_characters._install_builtin_characters = traced("install-characters")
server._skills.cleanup_stale_staging = lambda *a, **k: 0
server._ensure_incognito_project = traced("incognito")
server._bp_guide.seed_onboarding_on_startup = traced("onboarding")
server._bp_agent._run_claude_auth_probe = traced("auth-probe")
server._bp_workflows.adopt_on_startup = traced("workflow-adoption")
server._startup_memory_maintenance = traced("memory-maintenance")
server._hm_reconcile_stale_on_startup = traced("hivemind-reconcile")
server._session_label_enforcer_loop = traced("remote-label-loop")
server._enrollment_liveness_loop = traced("remote-liveness")
server._warmup_control_plane = traced("remote-warmup")
server._update_check_loop = traced("update-check")
import mc.wake_lock as wake_lock
wake_lock.start = traced("wake-lock")
real_reconcile = server._reconcile_pending_agent_log_entries
def reconcile():
    trace.append("agent-log-reconcile")
    return real_reconcile()
server._reconcile_pending_agent_log_entries = reconcile
real_start = ar.start_delegation_delivery
def start_delivery(*args, **kwargs):
    trace.append("delegation-start")
    kwargs["interval_s"] = 0.05
    return real_start(*args, **kwargs)
server._bp_agent.start_delegation_delivery = start_delivery
real_deliver_outbox = ar._deliver_outbox
def controlled_deliver_outbox(row):
    trace.append(f"transport:{row['event_id']}")
    if mode["value"] == "before_receipt":
        raise TimeoutError("test sender failed before receipt")
    real_deliver_outbox(row)
    trace.append(f"transport-accepted:{row['event_id']}")
    if mode["value"] == "lost_response":
        raise TimeoutError("test response lost after receiver acceptance")
ar._deliver_outbox = controlled_deliver_outbox
class FakeCodexRuntime:
    name = "codex"
    def model_supported(self, model):
        return True
    def build_command(self, **kwargs):
        return ["fake-codex", "--model", kwargs.get("model", ""),
                "--resume", kwargs.get("resume_id", "")]
    def dispatch(self, **kwargs):
        session = kwargs["session_dict"]
        assert session["_delivery_generation"] == 3
        assert session["agent_model"] == "saved-codex-model"
        assert session["requested_effort"] == "high"
        assert session["provider_session_id"] == "native-codex-parent-7"
        record(launch_path, {
            "provider": kwargs["project_id"] and session["provider"],
            "model": kwargs["model"], "effort": session["requested_effort"],
            "native_id": kwargs["resume_id"], "mc_session": kwargs["mc_session_id"],
            "generation": session["_delivery_generation"],
            "notify_session": session["_notify_session"],
            "notify_workflow": session["_notify_workflow"],
        })
        session["provider_session_id"] = kwargs["resume_id"]
        session["status"] = "completed"
        return None
ar._agent_runtime.get_runtime = lambda name: FakeCodexRuntime()

real_process_inbox = ar._process_inbox
def traced_process_inbox(row):
    if row.get("event_id") == "shutdown-fence":
        shutdown_inbox_entered.set()
    return real_process_inbox(row)
ar._process_inbox = traced_process_inbox
def fake_followup(project_id):
    handoff = {"project_id": project_id,
               "session_id": (request.get_json(silent=True) or {}).get("session_id", "")}
    record(handoff_path, handoff)
    return jsonify(ok=True, session_id=handoff["session_id"])
ar.agent_followup = fake_followup
server.boot(check_port=False)

@server.app.post("/test/seed")
def seed():
    data = request.get_json(force=True); payload = data["payload"]
    source_result = ar._delivery_store.record_completion_source(
        data["event_id"], data["project_id"], data["parent_session_id"], payload)
    enqueue_result = ar._delivery_store.enqueue(data["event_id"], data["project_id"],
                                                data["parent_session_id"], payload)
    if data.get("accepted"):
        ar._delivery_store.accept(data["event_id"], data["project_id"],
                                  data["parent_session_id"], payload)
    return jsonify(ok=True, source_result=source_result, enqueue_result=enqueue_result,
                   immediate=ar._delivery_store.status("outbox", data["event_id"], data["project_id"]))

@server.app.post("/test/mode")
def set_mode():
    mode["value"] = request.get_json(force=True)["value"]; return jsonify(ok=True)

@server.app.post("/test/drain")
def test_drain():
    def send(row):
        controlled_deliver_outbox(row)
    done = drain_once(ar._delivery_store, send_outbox=send,
                      process_inbox=ar._process_inbox)
    return jsonify(done=done)

@server.app.post("/test/live-parent")
def live_parent():
    ar.agent_sessions["parent-live"] = {
        "project_id": "p", "status": "completed", "provider": "codex",
        "agent_model": "live-model", "incognito": False}
    return jsonify(ok=True)

@server.app.post("/test/lock")
def hold_lock():
    manager = ar.get_manager("p"); manager.lock.acquire(); lock_held.set()
    lock_release.wait(5); manager.lock.release(); return jsonify(ok=True)

@server.app.post("/test/release-lock")
def release_lock():
    lock_release.set(); return jsonify(ok=True)

@server.app.post("/test/stop")
def stop_delivery():
    return jsonify(ar.stop_delegation_delivery(0.15))

@server.app.get("/test/state")
def state():
    rows = {}
    for event_id in ("before-receipt", "lost-response", "shutdown-fence"):
        rows[event_id] = {
            "outbox": ar._delivery_store.status("outbox", event_id, "p"),
            "inbox": ar._delivery_store.status("inbox", event_id, "p")}
    launches = []
    if launch_path.exists():
        launches = [json.loads(x) for x in launch_path.read_text(encoding="utf-8").splitlines()]
    handoffs = []
    if handoff_path.exists():
        handoffs = [json.loads(x) for x in handoff_path.read_text(encoding="utf-8").splitlines()]
    return jsonify(trace=trace, rows=rows, launches=launches,
                   handoffs=handoffs,
                   lock_held=lock_held.is_set(),
                   shutdown_inbox_entered=shutdown_inbox_entered.is_set(),
                   paths={
                       "data_root": str(server._DATA_ROOT),
                       "data_dir": str(server.DATA_DIR),
                       "delivery_db": str(ar._delivery_path),
                       "provider_env": str(server._DATA_ROOT / "data" / "provider_env.json"),
                       "pid_ledger": str(server._PID_LEDGER_PATH),
                       "home": str(Path.home()),
                   },
                   delivery_thread_alive=bool(ar._delivery_thread and ar._delivery_thread.is_alive()))

@server.app.get("/test/ready")
def ready():
    return jsonify(ok=True)

from werkzeug.serving import make_server
make_server("127.0.0.1", port, server.app, threaded=True).serve_forever()
'''

def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def _request(port, path, method="GET", body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())

def _wait_state(child, event_id, state, timeout=8):
    deadline = time.monotonic() + timeout
    current = child.request("/test/state")
    while current["rows"][event_id]["outbox"]["state"] != state:
        if time.monotonic() >= deadline:
            raise AssertionError(f"{event_id} never reached {state}: {current!r}")
        time.sleep(0.05)
        current = child.request("/test/state")
    return current

def _wait_inbox(child, event_id, state="submitted", timeout=8):
    deadline = time.monotonic() + timeout
    current = child.request("/test/state")
    while (current["rows"][event_id]["inbox"] or {}).get("state") != state:
        if time.monotonic() >= deadline:
            raise AssertionError(f"{event_id} inbox never reached {state}: {current!r}")
        time.sleep(0.05)
        current = child.request("/test/state")
    return current

def _wait_lost_response(child, event_id, timeout=8):
    deadline = time.monotonic() + timeout
    current = child.request("/test/state")
    while not (
            current["rows"][event_id]["outbox"] and
            current["rows"][event_id]["outbox"]["state"] == "pending" and
            current["rows"][event_id]["outbox"]["attempts"] >= 1 and
            current["rows"][event_id]["inbox"] is not None and
            "transport-accepted:" + event_id in current["trace"]):
        if time.monotonic() >= deadline:
            raise AssertionError(f"{event_id} lost-response fault was not observed: {current!r}")
        time.sleep(0.05)
        current = child.request("/test/state")
    return current

class _BootChild:
    def __init__(self, root, register_url, register_project, readiness_timeout_s=25):
        self.root, self.register_url, self.register_project = root, register_url, register_project
        self.readiness_timeout_s = readiness_timeout_s
        self.port, self.proc = _free_port(), None
        self.log = root / f"child-{self.port}.log"

    def start(self):
        temp = self.root / "temp"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8",
            "TEMP": str(temp), "TMP": str(temp),
            "HOME": str(self.root / "home"),
            "USERPROFILE": str(self.root / "userprofile"),
            "APPDATA": str(self.root / "appdata"),
            "LOCALAPPDATA": str(self.root / "localappdata"),
            "MC_DATA_DIR": str(self.root / "mc-data"),
            "MC_REMOTE_ENABLED": "0",
        }
        for key in ("TEMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "MC_DATA_DIR"):
            Path(env[key]).mkdir(parents=True, exist_ok=True)
        with self.log.open("w", encoding="utf-8") as stream:
            self.proc = subprocess.Popen(
                [sys.executable, "-c", CHILD, str(self.root), str(self.port)],
                cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT)
        self._register_immediately()
        deadline = time.monotonic() + self.readiness_timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(self.log.read_text(encoding="utf-8", errors="replace"))
            try:
                if _request(self.port, "/test/ready", timeout=0.5).get("ok"):
                    return
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        raise AssertionError("startup readiness timed out: " + self.log.read_text(encoding="utf-8", errors="replace"))

    def _register_immediately(self):
        if not self.register_url:
            raise AssertionError("MC_TEST_PROCESS_REGISTER_URL is required")
        body = json.dumps({
            "pid": self.proc.pid, "name": f"delegation startup harness {self.port}",
            "project_id": self.register_project,
            "command": "python -c <isolated delegation startup harness>",
        }).encode()
        req = urllib.request.Request(self.register_url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
            if response.status != 200 or not isinstance(result, dict) or result.get("ok") is not True:
                raise AssertionError(f"process registration failed: {response.status} {result!r}")

    def request(self, path, method="GET", body=None, timeout=8):
        return _request(self.port, path, method, body, timeout)

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=8)
        assert self.proc.poll() is not None

def _project_fixture(root):
    workspace = root / "project-workspace"; workspace.mkdir(parents=True)
    data = root / "mc-data" / "data"; projects = data / "projects"; projects.mkdir(parents=True)
    (data / "provider_env.json").write_text("{}", encoding="utf-8")
    (data / "SHARED_RULES.md").write_text("", encoding="utf-8")
    (root / "mc-data" / "config.json").write_text(json.dumps({
        "port": 5199, "projects_base": str(root),
        "auto_workspace_base": str(root / "auto-workspaces"),
        "shared_rules_path": str(data / "SHARED_RULES.md"),
        "default_provider": "codex", "agent_model": "project-default-wrong",
        "agent_effort": "low", "question_channel": "off",
        "keep_awake_enabled": False}), encoding="utf-8")
    (projects / "p.json").write_text(json.dumps({
        "id": "p", "name": "isolated", "project_path": str(workspace),
        "provider": "codex", "agent_model": "project-default-wrong",
        "agent_effort": "low", "default_character": ""}), encoding="utf-8")
    (projects / "p_agent_log.json").write_text(json.dumps([{
        "session_id": "parent-cold", "status": "completed", "provider": "codex",
        "provider_session_id": "native-codex-parent-7",
        "agent_model": "saved-codex-model", "model": "telemetry-wrong",
        "pinned_model": "", "requested_effort": "high", "source": "agent",
        "spawned_by_session_id": "grandparent", "trigger_type": "workflow",
        "trigger_id": "run-7:step-2", "incognito": False, "ts": "2"}]), encoding="utf-8")
    # Seed a recreated project generation so the real runtime dispatch must
    # carry a generation greater than 1; revoke+recreate yields generation 3
    # because revocation itself advances the generation. The child then opens
    # this exact production path.
    delivery = DeliveryStore(data / "delegation_delivery.sqlite3")
    delivery.project_generation("p")
    delivery.revoke_project("p")
    delivery.recreate_project("p")

def test_registration_failure_reaps_owned_child(tmp_path, monkeypatch):
    _project_fixture(tmp_path)
    child = _BootChild(tmp_path, "http://127.0.0.1:9/register", "test")
    monkeypatch.setattr(child, "_register_immediately",
                        lambda: (_ for _ in ()).throw(RuntimeError("registration denied")))
    try:
        with pytest.raises(RuntimeError, match="registration denied"):
            child.start()
    finally:
        child.stop()
    assert child.proc is not None and child.proc.poll() is not None

def test_readiness_failure_reaps_owned_child(tmp_path, monkeypatch):
    _project_fixture(tmp_path)
    child = _BootChild(tmp_path, "unused", "test", readiness_timeout_s=0.2)
    monkeypatch.setattr(child, "_register_immediately", lambda: None)
    def no_ready(*args, **kwargs):
        raise urllib.error.URLError("readiness intentionally unavailable")
    monkeypatch.setattr(sys.modules[__name__], "_request", no_ready)
    try:
        with pytest.raises(AssertionError, match="startup readiness timed out"):
            child.start()
    finally:
        child.stop()
    assert child.proc is not None and child.proc.poll() is not None

def test_full_startup_delivery_survives_restarts_and_fences_shutdown(tmp_path):
    register_url = os.environ.get("MC_TEST_PROCESS_REGISTER_URL")
    register_project = os.environ.get("MC_TEST_PROCESS_PROJECT")
    if not register_url:
        pytest.skip("full-startup subprocess harness requires explicit MC_TEST_PROCESS_REGISTER_URL")
    if not register_project:
        pytest.fail("MC_TEST_PROCESS_PROJECT is required when registration is enabled")
    _project_fixture(tmp_path); children = []
    payload = {"event_id": "before-receipt", "child_session_id": "child-1",
               "status": "completed", "task": "task", "summary": "result",
               "message": "result", "provider": "codex", "model": "child-model",
               "delivery_generation": 3}
    try:
        first = _BootChild(tmp_path, register_url, register_project); children.append(first); first.start()
        boot = first.request("/test/state")
        assert boot["trace"].index("agent-log-reconcile") < boot["trace"].index("delegation-start")
        assert {"scheduler", "hivemind", "coordination", "guardian", "auth-probe",
                "memory-maintenance", "remote-warmup", "update-check", "wake-lock"} <= set(boot["trace"])
        assert boot["launches"] == []
        root = tmp_path.resolve()
        for path in boot["paths"].values():
            assert Path(path).resolve().is_relative_to(root), (path, root)
        first.request("/test/mode", "POST", {"value": "before_receipt"})
        seed_response = first.request("/test/seed", "POST", {"event_id": "before-receipt", "project_id": "p",
                                                               "parent_session_id": "parent-cold", "payload": payload})
        assert seed_response["enqueue_result"] is True, seed_response
        seeded = first.request("/test/state")
        assert seeded["rows"]["before-receipt"]["outbox"] is not None, {
            "rows": seeded["rows"], "paths": seeded["paths"]}
        first.stop()

        second = _BootChild(tmp_path, register_url, register_project); children.append(second); second.start()
        state = _wait_state(second, "before-receipt", "delivered")
        state = _wait_inbox(second, "before-receipt")
        assert state["rows"]["before-receipt"]["outbox"]["state"] == "delivered"
        assert state["rows"]["before-receipt"]["inbox"]["state"] == "submitted"
        assert len(state["launches"]) == 1
        assert state["launches"][0] == {
                "effort": "high", "generation": 3, "mc_session": "parent-cold",
            "model": "saved-codex-model", "native_id": "native-codex-parent-7",
            "notify_session": "grandparent",
            "notify_workflow": {"run_id": "run-7", "step": "step-2"},
            "provider": "codex"}

        lost = dict(payload, event_id="lost-response", child_session_id="child-2")
        second.request("/test/mode", "POST", {"value": "lost_response"})
        second.request("/test/seed", "POST", {"event_id": "lost-response", "project_id": "p",
                                              "parent_session_id": "parent-cold", "payload": lost})
        after_lost = _wait_lost_response(second, "lost-response")
        assert after_lost["rows"]["lost-response"]["inbox"]["state"] == "submitted"
        assert after_lost["rows"]["lost-response"]["outbox"]["state"] == "pending"
        assert len(after_lost["launches"]) == 1
        assert len(after_lost["handoffs"]) == 1
        second.stop()

        third = _BootChild(tmp_path, register_url, register_project); children.append(third); third.start()
        third.request("/test/mode", "POST", {"value": "normal"})
        recovered = _wait_state(third, "lost-response", "delivered")
        recovered = _wait_inbox(third, "lost-response")
        assert recovered["rows"]["lost-response"]["outbox"]["state"] == "delivered"
        assert recovered["rows"]["lost-response"]["inbox"]["state"] == "submitted"
        assert len(recovered["launches"]) == 1
        assert len(recovered["handoffs"]) == 1
        handoffs_before_shutdown = len(recovered["handoffs"])

        third.request("/test/live-parent", "POST")
        shutdown = dict(payload, event_id="shutdown-fence", child_session_id="child-3")
        holder = __import__("threading").Thread(target=lambda: third.request("/test/lock", "POST"))
        holder.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if third.request("/test/state")["lock_held"]:
                break
            time.sleep(0.05)
        assert third.request("/test/state")["lock_held"] is True
        third.request("/test/seed", "POST", {"event_id": "shutdown-fence", "project_id": "p",
                                              "parent_session_id": "parent-live", "payload": shutdown,
                                              "accepted": True})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if third.request("/test/state")["shutdown_inbox_entered"]:
                break
            time.sleep(0.05)
        assert third.request("/test/state")["shutdown_inbox_entered"] is True
        stop_result = third.request("/test/stop", "POST")
        assert stop_result["requested"] is True
        assert stop_result["timed_out"] is True
        assert stop_result["joined"] is False
        assert stop_result["alive"] is True
        third.request("/test/release-lock", "POST"); holder.join(8)
        assert not holder.is_alive()
        deadline = time.monotonic() + 8
        final = third.request("/test/state")
        while final["delivery_thread_alive"]:
            if time.monotonic() >= deadline:
                raise AssertionError(f"delivery loop did not join after lock release: {final!r}")
            time.sleep(0.05)
            final = third.request("/test/state")
        assert len(final["handoffs"]) == handoffs_before_shutdown
        assert final["rows"]["shutdown-fence"]["inbox"]["state"] in ("pending", "blocked", "uncertain")
    finally:
        for child in reversed(children):
            child.stop()
