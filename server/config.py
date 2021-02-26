from pathlib import Path
import json
import os
import subprocess
from typing import Optional, Tuple

import oauth


# Required
REPO_URL = os.environ.get("REPO_URL", ".")
REPO_NAME = os.environ.get("REPO_NAME", "testing")
BASE_URL = os.environ.get("BASE_URL", "http://localhost")

# Optional
TASKS_FILE = Path(os.environ.get("TASKS_FILE", "tasks.py"))
mounts = os.environ.get("ADDITIONAL_MOUNTS")
ADDITIONAL_MOUNTS = mounts.split(",") if mounts else []

# Authentication
OAUTH_ENABLED = False
if os.environ.get("GITHUB_CLIENT_ID"):
    OAUTH_ENABLED = True
    OAUTH_SERVER = oauth.OauthServer(
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        "https://api.github.com/user",
        os.environ["GITHUB_CLIENT_ID"],
        os.environ["GITHUB_CLIENT_SECRET"],
    )
    AUTHORIZED_USERS = os.environ["GITHUB_AUTHORIZED_USERS"].split(",")

# Advanced
DATA_PATH = Path("data")
REPO_PATH = DATA_PATH / "repo"
LOGS_PATH = DATA_PATH / "logs"
WORK_PATH = DATA_PATH / "workspaces"
LOGFILE = "output.log"
STATEFILE = "state.json"
ISOLATE_PYTHON = os.environ.get("ISOLATE_PYTHON")  # Only relevant for running minimalci on itself


# Introspection when running in docker
def get_self_container_id() -> Optional[str]:
    if Path("/.dockerenv").is_file():
        return os.environ["HOSTNAME"]
    return None


def get_image_from_container_id(container_id: str) -> Tuple[str, str]:
    data = json.loads(subprocess.check_output(["docker", "inspect", container_id]).decode())
    assert len(data) == 1
    _, image_id = data[0]["Image"].split(":")
    image_name = data[0]["Config"]["Image"]
    return str(image_name), str(image_id)


def get_external_mount_point(container_id: str, internal_path: str) -> Path:
    data = json.loads(subprocess.check_output(["docker", "inspect", container_id]).decode())
    assert len(data) == 1
    mounts = data[0]["Mounts"]
    for mount in mounts:
        if mount.get("Destination") == internal_path:
            return Path(mount["Source"])
    raise Exception(f"External mount point not found for {internal_path}")


SELF_CONTAINER_ID = get_self_container_id()
SELF_IMAGE_NAME, SELF_IMAGE_ID = get_image_from_container_id(SELF_CONTAINER_ID) if SELF_CONTAINER_ID else ("CONTAINER_NAME_NOT_DETECTED", "")
TASKRUNNER_IMAGE = SELF_IMAGE_ID or "minimalci"
EXTERNAL_DATA_MOUNT_POINT = get_external_mount_point(SELF_CONTAINER_ID, str(DATA_PATH.absolute())) if SELF_CONTAINER_ID else DATA_PATH.absolute()
EXTERNAL_SSH_MOUNT_POINT = get_external_mount_point(SELF_CONTAINER_ID, str(Path("~/.ssh").expanduser())) if SELF_CONTAINER_ID else Path("~/.ssh").expanduser()
