import time
import threading
import os
from pathlib import Path
import queue
import json
import concurrent.futures
import datetime
from typing import Tuple, Iterator, Dict, List, Any, Callable, Set, Optional
import string
import secrets
import functools
import subprocess
import logging
import shutil
import gzip

from flask import Flask, request, escape, Response, render_template, session
import flask

from minimalci.executors import run_command, ProcessError
from minimalci.tasks import StateSnapshot, TaskSnapshot, Status, State

import ansi2html
import config
import oauth

DEBUG = False

app = Flask(__name__)
app.secret_key = secrets.token_hex(64)
app.config.update(
    SESSION_COOKIE_SECURE=False if DEBUG else True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',  # Default if unset
)

SCAN_TRIGGER = threading.Event()
IS_INHIBITED = False


# Handle authentication / authorization


@app.route("/login")
def login() -> Response:
    if not config.OAUTH_ENABLED:
        return Response("Disabled", 404)
    url, session_state = oauth.begin_oauth(config.OAUTH_SERVER)
    session["oauth_state"] = session_state
    return Response("Redirecting...", 302, {"Location": url})


@app.route("/callback")
def login_callback() -> Response:
    if not config.OAUTH_ENABLED:
        return Response("Disabled", 404)
    access_token = oauth.finish_oauth(
        config.OAUTH_SERVER,
        str(request.args.get("code")),
        str(request.args.get("state")),
        str(session.get("oauth_state")),
    )
    username = oauth.get_username(config.OAUTH_SERVER, access_token)
    if username in config.AUTHORIZED_USERS:
        session["username"] = username
        return Response("Redirecting...", 302, {"Location": session.pop("redirected_from", "/")})
    return Response("User not authorized", 403)


@app.route("/logout")
def logout() -> Response:
    session.clear()
    if not config.OAUTH_ENABLED:
        return Response("Disabled", 404)
    return Response("Logged out", 303, {"Location": "/"})


def is_logged_in() -> bool:
    if session.get("username"):
        return True
    return False


def require_authorization(redirect: bool = False, require_logged_in: bool = False) -> Callable[..., Any]:
    def authorized_wrapper(f: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(f)
        def decorator(*args: Any, **kwargs: Any) -> Any:
            if DEBUG:
                session["username"] = "test"
            if is_logged_in():
                return f(*args, **kwargs)
            if not config.OAUTH_ENABLED:
                if require_logged_in:
                    return "Disabled", 400
                return f(*args, **kwargs)
            if redirect:
                session["redirected_from"] = request.path
                return '<a href="/login">Click here to login with Github</a>'
            return "Unauthenticated", 401
        return decorator
    return authorized_wrapper


# Utils


class ClientError(Exception):
    status_code = 400


@app.errorhandler(ClientError)
def client_error(error: ClientError) -> Tuple[str, int]:
    return escape(str(error)), error.status_code


def verify_identifier(identifier: str) -> None:
    timestamp, sha = identifier.split("_")
    int(timestamp)
    # TODO: Improve error messages
    if len(sha) != 40:
        raise ClientError("Invalid sha")
    legal_chars = string.ascii_letters + string.digits
    for c in sha:
        if c not in legal_chars:
            raise ClientError("Invalid sha")


def gzip_response_if_supported(response: Response) -> Response:
    if 'gzip' not in request.headers.get('Accept-Encoding', ''):
        return response
    response.data = gzip.compress(response.data)
    response.headers.update({"Content-Encoding": "gzip", "Content-Length": len(response.data)})  # type: ignore
    return response


# Stream handling


def json_file_to_queue(path: Path, q: "queue.Queue[str]", kill_signal: threading.Event) -> None:
    mtime = 0.0
    while True:
        if kill_signal.is_set():
            return
        try:
            new_mtime = path.stat().st_mtime
            if new_mtime != mtime:
                text = path.read_text()
                data = json.loads(text)
                q.put(data)
                mtime = new_mtime
        except Exception:
            pass
        time.sleep(1)


def tail_to_queue(path: Path, from_line: int, q: "queue.Queue[str]", kill_signal: threading.Event) -> None:
    # Wait if file does not exist
    while not kill_signal.is_set():
        if path.is_file():
            break
        time.sleep(0.5)

    tail = ["tail", "-n", "+{}".format(from_line), "-f", str(path)]
    try:
        run_command(tail, should_print=False, output_queue=q, kill_signal=kill_signal)
    except ProcessError:
        pass


def get_stage(line: str) -> str:
    _, stage, *_ = line.split()
    return escape(stage)


def sse_generator(from_line: int, base_path: Path) -> Iterator[str]:
    kill_signal = threading.Event()
    q: "queue.Queue[str]" = queue.Queue()
    log_path = base_path / config.LOGFILE
    state_path = base_path / config.STATEFILE
    with concurrent.futures.ThreadPoolExecutor(2) as e:
        f1 = e.submit(tail_to_queue, log_path, from_line, q, kill_signal)
        f2 = e.submit(json_file_to_queue, state_path, q, kill_signal)
        try:
            yield ":connected\n\n"
            line_number = from_line
            while True:
                try:
                    item = q.get(timeout=10)
                except queue.Empty:
                    # ping to check if client is still connected
                    yield ":ping\n\n"
                    continue
                if isinstance(item, dict):
                    data = "event: state\n"
                    data += "data: {}\n".format(json.dumps(item))
                    data += "\n"
                    yield data
                else:
                    data = "id: {}\n".format(line_number)
                    data += "event: line\n"
                    data += "data: {}\n".format(
                        json.dumps([
                            get_stage(item),
                            ansi2html.escaped(item)
                        ])
                    )
                    data += "\n"
                    yield data
                    line_number += 1
        finally:
            kill_signal.set()
            f1.result()
            f2.result()


@app.route("/stream/<identifier>")
@require_authorization()
def stream(identifier: str) -> Response:
    verify_identifier(identifier)
    base_path = config.LOGS_PATH / identifier
    from_line = int(request.headers.get("last-event-id", request.args.get("id", 1)))
    return Response(sse_generator(from_line, base_path), 200, mimetype="text/event-stream")


# Fancy visulas


def get_duration(snapshot: TaskSnapshot) -> str:
    if snapshot.finished and snapshot.started:
        return str(datetime.timedelta(seconds=(int(snapshot.finished - snapshot.started))))
    return ""


def depth_in_tree(state: StateSnapshot, task_name: str) -> int:
    for task in state.tasks:
        if task.name == task_name:
            if not task.run_after:
                return 0
            return max(
                [
                    depth_in_tree(state, parent_task_name)
                    for parent_task_name in task.run_after
                ]
            ) + 1
    raise LookupError


# Views


@app.route("/logs/<identifier>/")
@app.route("/logs/<identifier>")
@require_authorization(redirect=True)
def logs(identifier: str) -> Response:
    verify_identifier(identifier)
    base_path = config.LOGS_PATH / identifier
    logfile = base_path / config.LOGFILE
    statefile = base_path / config.STATEFILE
    if not statefile.is_file():
        return Response("Page not found", 404)

    # Wait for tasks to be enumerated/started
    # This is needed due to server side generated html of all tasks
    while not StateSnapshot.load(statefile).tasks and time.time() - StateSnapshot.load(statefile).started < 10:
        time.sleep(0.5)

    lines = []
    if logfile.is_file():
        lines = [
            (get_stage(line), ansi2html.escaped(line))
            for line in logfile.read_text().splitlines()
        ]
    line_number = len(lines) + 1  # +1 to match tail -f format
    state = StateSnapshot.load(statefile)
    return gzip_response_if_supported(
        Response(
            render_template(
                "log.html",
                title=config.REPO_NAME,
                state=state,
                stream=f"/stream/{identifier}?id={line_number}",
                lines=lines,  # lines are not autoescaped, must be manually escaped
                get_duration=get_duration,
                is_logged_in=is_logged_in(),
                depth_in_tree=depth_in_tree,
                DEBUG=DEBUG,
            ), 200
        )
    )


def get_state_snapshots(limit: Optional[int] = None, print_errors: bool = False) -> List[Tuple[Path, StateSnapshot]]:
    snapshots: List[Tuple[Path, StateSnapshot]] = []
    directories = sorted(list(config.LOGS_PATH.iterdir()), reverse=True)
    for directory in directories:
        if limit and len(snapshots) >= limit:
            break
        statefile = directory / config.STATEFILE
        if statefile.is_file():
            try:
                snapshots.append((statefile, StateSnapshot.load(statefile)))
            except Exception as e:
                if print_errors:
                    print(f"ERROR: Failed to load {statefile}: {e}")
        else:
            if print_errors:
                print(f"ERROR: {config.STATEFILE} not found in {directory}")
    return snapshots


@app.route("/")
@require_authorization(redirect=True)
def repo_index() -> Response:
    should_show_all = request.args.get("show") == "all"
    limit = None if should_show_all else 40
    snapshots = get_state_snapshots(limit=limit)
    is_limited_view = limit == len(snapshots)

    title = config.REPO_NAME
    builds = []
    tags = get_all_tags()
    for path, snapshot in snapshots:
        timestamp = str(datetime.datetime.fromtimestamp(int(snapshot.started)).isoformat()) + "Z"
        finished = int(snapshot.finished) if snapshot.finished else int(time.time())
        duration = datetime.timedelta(seconds=(finished - int(snapshot.started)))
        duration_str = str(duration)
        if duration >= datetime.timedelta(days=1):
            duration_str = f"{duration.days} days"
        builds.append({
            "branch": snapshot.branch,
            "link": f"logs/{snapshot.identifier}",
            "timestamp": timestamp,
            "duration": duration_str,
            "status": snapshot.status,
            "sha": snapshot.commit[:8],
            "tags": tags.get(snapshot.commit, []),
        })
    return gzip_response_if_supported(
        Response(
            render_template(
                "builds.html",
                title=title,
                image_name=config.SELF_IMAGE_NAME,
                builds=builds,
                is_logged_in=is_logged_in(),
                is_inhibited=IS_INHIBITED,
                DEBUG=DEBUG,
                is_limited_view=is_limited_view,
            ), 200
        )
    )


# Actions


def get_external_url(identifier: str) -> str:
    return f"/logs/{identifier}"


@app.route("/trigger", methods=["GET", "POST"])
def trigger() -> Response:
    if IS_INHIBITED:
        return Response("Inhibited", 400)
    SCAN_TRIGGER.set()
    if request.method == "POST":
        return Response("Looking for changes in remote repo", 303, {"Location": "/"})
    return Response("Looking for changes in remote repo", 200)


@app.route("/kill/<identifier>", methods=["POST"])
@require_authorization(require_logged_in=True)
def kill(identifier: str) -> Response:
    for state_path, state in get_state_snapshots():
        if state.identifier == identifier:
            try:
                subprocess.check_call(["docker", "kill", "-s", "SIGTERM", state.identifier])
            except Exception:
                # Refetch state to lower likelihood of race condition
                updated_state = StateSnapshot.load(state_path)
                if not updated_state.finished:
                    updated_state.finished = time.time()
                    updated_state.status = Status.failed.name
                    updated_state.save(state_path)
                    return Response("Container not running. Overall status manually set to FAILED.", 200)
                else:
                    return Response("Container not running", 400)
            return Response(f"Sent SIGTERM to container", 303, {"Location": f"/logs/{identifier}"})
    return Response("Identifier not found", 404)


@app.route("/rerun/<identifier>", methods=["POST"])
@require_authorization(require_logged_in=True)
def rerun(identifier: str) -> Response:
    if IS_INHIBITED:
        return Response("Inhibited", 400)
    for state_path, state in get_state_snapshots():
        if state.identifier == identifier:
            new_identifier = start_taskrunner_in_docker(state.commit, state.branch)
            return Response("Rerunning", 303, {"Location": f"/logs/{new_identifier}"})
    return Response("Identifer not found", 404)


@app.route("/inhibit", methods=["POST"])
@require_authorization(require_logged_in=True)
def inhibit() -> Response:
    global IS_INHIBITED
    IS_INHIBITED = True
    return Response("Inhibited", 303, {"Location": "/"})


@app.route("/remove_inhibition", methods=["POST"])
@require_authorization(require_logged_in=True)
def remove_inhibit() -> Response:
    global IS_INHIBITED
    IS_INHIBITED = False
    return Response("Removed inhibition", 303, {"Location": "/"})


# Background thread logic


def git_fetch() -> None:
    logging.info("Fetching updates from remote")
    run_command(["git", "-C", str(config.REPO_PATH), "fetch", "--prune", "--prune-tags", "-v"], print_prefix="git  ")


def get_all_branches() -> Set[Tuple[str, str]]:
    lines = subprocess.check_output(["git", "-C", str(config.REPO_PATH), "show-ref"]).decode().splitlines()
    remote_branches: Set[Tuple[str, str]] = set()
    for line in lines:
        commit, ref = line.split()
        branch_prefix = "refs/remotes/origin/"
        if ref.startswith(branch_prefix) and not ref.endswith("HEAD"):
            branch_name = ref.replace(branch_prefix, "")
            remote_branches.add((branch_name, commit))
    return remote_branches


def get_all_tags() -> Dict[str, List[str]]:
    tags: Dict[str, List[str]] = {}
    lines = subprocess.check_output(["git", "-C", str(config.REPO_PATH), "show-ref"]).decode().splitlines()
    for line in lines:
        commit, ref = line.split()
        tag_prefix = "refs/tags/"
        if ref.startswith(tag_prefix):
            tag = ref.replace(tag_prefix, "")
            tags[commit] = tags.get(commit, []) + [tag]
    return tags


def get_new_branches() -> Set[Tuple[str, str]]:
    local_builds = set((snapshot.branch, snapshot.commit) for path, snapshot in get_state_snapshots())
    remote_branches = get_all_branches()
    new_branches = remote_branches.difference(local_builds)
    return new_branches


def checkout_repo(output_path: Path, branch: str, git_sha: str) -> None:
    if len(list(output_path.iterdir())) > 0:
        raise Exception("Workdir not empty")
    subprocess.check_call(["cp", str(config.REPO_PATH / ".git"), str(output_path), "-r"])
    subprocess.check_call(["git", "checkout", git_sha, "-f"], cwd=output_path, stderr=subprocess.DEVNULL)


def safe_name(name: str) -> str:
    legal_chars = string.ascii_letters + string.digits + "-_"
    return "".join([char if char in legal_chars else "_" for char in name])


def start_taskrunner_in_docker(commit: str, branch: str) -> str:
    if IS_INHIBITED:
        raise Exception("Tried to start taskrunner while inhibited")
    identifier = f"{int(time.time())}_{commit}"
    logdir = config.LOGS_PATH / identifier
    # Loop to guarantee unique identifier
    while logdir.is_dir():
        time.sleep(1)
        identifier = f"{int(time.time())}_{commit}"
        logdir = config.LOGS_PATH / identifier
    workdir = config.WORK_PATH / identifier

    os.makedirs(logdir)
    os.makedirs(workdir)
    checkout_repo(workdir, branch, commit)

    external_logdir = config.EXTERNAL_DATA_MOUNT_POINT / logdir.relative_to(config.DATA_PATH)
    external_workdir = config.EXTERNAL_DATA_MOUNT_POINT / workdir.relative_to(config.DATA_PATH)

    # Create empty state to indicate build has been initiated
    state = State(
        branch=branch,
        commit=commit,
        identifier=identifier,
    )
    state.snapshot().save(logdir / config.STATEFILE)

    command = [
        "docker",
        "run",
        "--rm",
        "-d",
        "--name", identifier,
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{config.EXTERNAL_SSH_MOUNT_POINT}:/root/.ssh:ro",
        "-v", f"{external_logdir}:/logdir",
        "-v", f"{external_workdir}:/workdir",
        "--workdir", "/workdir",
    ]
    for additional_mount in config.ADDITIONAL_MOUNTS:
         command += [
             "-v", additional_mount,
         ]
    command += [
        config.TASKRUNNER_IMAGE,
        "python3",
        "-u",  # Unbuffered output
    ]
    if config.ISOLATE_PYTHON:
        # Isolate from current working dir
        # To avoid problems running minimalci on itself
        command += ["-I"]
    command += [
        "-m", "minimalci.taskrunner",
        "--commit", commit,
        "--branch", branch,
        "--identifier", identifier,
        "--repo-name", config.REPO_NAME,
        "--log-url", config.BASE_URL.rstrip("/") + f"/logs/{identifier}",
        "--logdir", "/logdir",
        "--file", str(config.TASKS_FILE),
    ]
    subprocess.check_call(command)
    logging.info(f"Started commit {commit}[{branch}] in container {identifier}")
    return identifier


def workspace_cleanup() -> None:
    KEEP_WORKSPACES_SECONDS = 10
    snapshots = get_state_snapshots()
    for workspace in config.WORK_PATH.iterdir():
        for _, snapshot in snapshots:
            if workspace.name == snapshot.identifier:
                if snapshot.finished:
                    if time.time() - snapshot.finished > KEEP_WORKSPACES_SECONDS:
                        try:
                            print(f"Deleting workspace {workspace}")
                            shutil.rmtree(workspace)
                        except Exception as e:
                            print(f"Error deleting old workspace {workspace}\n{e}")


def init() -> None:
    config.LOGS_PATH.mkdir(exist_ok=True)
    ssh_path = Path("~/.ssh").expanduser()
    ssh_path.mkdir(exist_ok=True)
    if len(list(ssh_path.iterdir())) == 0:
        subprocess.check_call(["ssh-keygen", "-f", str(ssh_path / "id_rsa"), "-P", ""])
        pub_key = (ssh_path / "id_rsa.pub").read_text()
        logging.info(f"\n\n{pub_key}\n")
        (ssh_path / "config").write_text("Host *\n  StrictHostKeyChecking=accept-new")
    # Clone repo
    if not (config.REPO_PATH / ".git").is_dir():
        subprocess.check_call(["git", "clone", config.REPO_URL, str(config.REPO_PATH)])
    # Verify repo
    if subprocess.check_output(["git", "-C", str(config.REPO_PATH), "remote"]).decode().strip() != "origin":
        raise Exception("git remote != origin")
    if subprocess.check_output(["git", "-C", str(config.REPO_PATH), "remote", "get-url", "origin"]).decode().strip() != config.REPO_URL:
        raise Exception("git remote get-url origin != REPO_URL")
    # Get state snapshots to print error messages on init
    get_state_snapshots(print_errors=True)

    # server_state_path = config.DATA_PATH / "server_state.json"
    # server_state = load_dataclass(ServerState, server_state_path)
    # save_dataclass(ServerState, server_state_path, server_state)


def background_thread() -> None:
    while True:
        SCAN_TRIGGER.wait()
        SCAN_TRIGGER.clear()
        try:
            git_fetch()
            for branch, commit in get_new_branches():
                start_taskrunner_in_docker(commit, branch)
            workspace_cleanup()
        except Exception as e:
            print(f"ERROR: Background Thread Failed\n{e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init()

    SCAN_TRIGGER.set()
    threading.Thread(target=background_thread, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
