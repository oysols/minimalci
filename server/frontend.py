import time
import threading
import os
from pathlib import Path
import queue
import json
import concurrent.futures
import datetime
from typing import Tuple, Iterator, Dict, List, Any, Callable
import string
import secrets
import functools

from flask import Flask, request, escape, Response, render_template, session
import flask

from minimalci.executors import run_command, NonZeroExit
import ansi2html
import config
import oauth

app = Flask(__name__)
app.secret_key = secrets.token_hex(64)

TRIGGER = threading.Event()


# Handle authentication / authorization


@app.route("/login")
def login():  # type: ignore
    if not config.OAUTH_ENABLED:
        return "Disabled", 404
    url, session_state = oauth.begin_oauth(config.OAUTH_SERVER)
    session["oauth_state"] = session_state
    return flask.redirect(url, code=302)


@app.route("/callback")
def login_callback():  # type: ignore
    if not config.OAUTH_ENABLED:
        return "Disabled", 404
    access_token = oauth.finish_oauth(
        config.OAUTH_SERVER,
        str(request.args.get("code")),
        str(request.args.get("state")),
        str(session.get("oauth_state")),
    )
    username = oauth.get_username(config.OAUTH_SERVER, access_token)
    if username in config.AUTHORIZED_USERS:
        session["authorized_username"] = username
        return flask.redirect(session.pop("redirected_from", "/"), code=302)
    return "User not authorized", 403


@app.route("/logout")
def logout():  # type: ignore
    if not config.OAUTH_ENABLED:
        return "Disabled", 404
    session.clear()
    return "Logged out", 200


def authorized(redirect: bool = False) -> Callable[..., Any]:
    def authorized_wrapper(f: Callable[..., Any]) -> Callable[..., Any]:
        if not config.OAUTH_ENABLED:
            return f

        @functools.wraps(f)
        def decorator(*args: Any, **kwargs: Any) -> Any:
            if session.get("authorized_username"):
                return f(*args, **kwargs)
            if redirect:
                session["redirected_from"] = request.path
                return f"""<a href="/login">Click here to login with Github</a>"""
            return "Unauthenticated", 401
        return decorator
    return authorized_wrapper


# Utils


class ClientError(Exception):
    status_code = 400


@app.errorhandler(ClientError)
def client_error(error: ClientError) -> Tuple[str, int]:
    return escape(str(error)), error.status_code


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
                q.put(data["tasks"])
                mtime = new_mtime
        except Exception:
            pass
        time.sleep(1)


def tail_to_queue(path: Path, from_line: int, q: "queue.Queue[str]", kill_signal: threading.Event) -> None:
    tail = ["tail", "-n", "+{}".format(from_line), "-f", str(path)]
    try:
        run_command(tail, output_queue=q, kill_signal=kill_signal)
    except NonZeroExit:
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
                    line = q.get(timeout=10)
                except queue.Empty:
                    # ping to check if client is still connected
                    yield ":ping\n\n"
                    continue
                if isinstance(line, list):
                    data = "event: state\n"
                    data += "data: {}\n".format(json.dumps(line))
                    data += "\n"
                    yield data
                else:
                    data = "id: {}\n".format(line_number)
                    data += "event: line\n"
                    data += "data: {}\n".format(
                        json.dumps([
                            get_stage(line),
                            ansi2html.escaped(line)
                        ])
                    )
                    data += "\n"
                    yield data
                    line_number += 1
        finally:
            kill_signal.set()
            f1.result()
            f2.result()


def verify_sha(sha: str) -> None:
    if len(sha) != 40:
        raise ClientError("Invalid sha")
    legal_chars = string.ascii_letters + string.digits
    for c in sha:
        if c not in legal_chars:
            raise ClientError("Invalid sha")


def get_logs() -> List[Dict[str, Any]]:
    logs = []
    for directory in config.LOGS_PATH.iterdir():
        statefile = directory / config.STATEFILE
        if statefile.is_file():
            data: Dict[str, Any] = json.loads(statefile.read_text())
            logs.append(data)
    return logs


# Endpoints


@app.route("/stream/<sha>")
@authorized()
def stream(sha: str) -> Tuple[Response, int]:
    verify_sha(sha)
    base_path = Path(os.path.join("./logs", sha))
    from_line = int(request.headers.get("last-event-id", request.args.get("id", 1)))
    return Response(sse_generator(from_line, base_path), mimetype="text/event-stream"), 200


@app.route("/logs/")
@app.route("/logs")
@app.route("/")
@authorized(redirect=True)
def repo_index() -> Tuple[str, int]:
    logs = sorted(get_logs(), key=lambda data: data.get("meta", {}).get("started", 0), reverse=True)
    title = config.REPO_NAME
    builds = []
    for data in logs:
        meta = data.get("meta", {})
        timestamp = datetime.datetime.fromtimestamp(int(meta.get("started", 0)))
        tasks = data.get("tasks", [])
        status = "running"
        if len(tasks) == 0:
            status = "skipped"
        elif all([task.get("status") == "success" for task in tasks]):
            status = "success"
        elif any([task.get("status") == "failed" for task in tasks]):
            status = "failed"
        # TODO: delete all meta
        builds.append({
            "branch": meta.get("branch") or data.get("branch", "error"),
            "link": "logs/{}".format(meta.get("commit") or data.get("commit", "error")),
            "timestamp": timestamp.isoformat(),
            "status": status,
            "sha": meta.get("commit", "")[:8] or data.get("commit", "error")[:8],
        })
    return render_template("builds.html", builds=builds, title=title, show_logout=config.OAUTH_ENABLED), 200


@app.route("/trigger", methods=["GET", "POST"])
def trigger() -> Tuple["str", int]:
    TRIGGER.set()
    return "Triggered", 200


@app.route("/logs/<sha>")
@app.route("/logs/<sha>/")
@authorized(redirect=True)
def logs(sha: str) -> Tuple[str, int]:
    verify_sha(sha)
    base_path = Path(os.path.join("./logs", sha))
    logfile = base_path.joinpath(config.LOGFILE)
    statefile = base_path.joinpath(config.STATEFILE)
    timeout = time.time() + 5
    while not statefile.is_file() or not logfile.is_file():
        time.sleep(1)
        if time.time() > timeout:
            return "Page not found", 404
    lines = [
        (get_stage(line), ansi2html.escaped(line))
        for line in logfile.read_text().splitlines()
    ]
    line_number = len(lines) + 1  # +1 to match tail -f format
    data = json.loads(statefile.read_text())
    tasks = data["tasks"]
    title = config.REPO_NAME
    # TODO: remove all meta
    meta = data.get("meta", {})
    branch = meta.get("branch") or data.get("branch")
    # TODO: assert sha == meta["commit"]
    # lines are not autoescaped, must be manually escaped
    return render_template("log.html", sha=sha, tasks=tasks, lines=lines, line_number=line_number, title=title, branch=branch), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
