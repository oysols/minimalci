from pathlib import Path
import json
import os
import subprocess
from typing import Optional

import oauth


# Required
REPO_URL = os.environ.get("REPO_URL", ".")
REPO_NAME = os.environ.get("REPO_NAME", "testing")
BASE_URL = os.environ.get("BASE_URL", "http://localhost")


# Optional
TASKS_FILE = Path(os.environ.get("TASKS_FILE", "tasks.py"))
DOCKER_REGISTRY = os.environ.get("DOCKER_REGISTRY")
if DOCKER_REGISTRY:  # Docker registry auto login at import time
    subprocess.check_call(
        [
            "docker",
            "login",
            DOCKER_REGISTRY,
            "-u", os.environ.get("DOCKER_USER", ""),
            "-p", os.environ.get("DOCKER_PASS", ""),
        ]
    )

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
SECRETS_PATH = DATA_PATH / "secrets"
WORK_PATH = DATA_PATH / "workspaces"
LOGFILE = "output.log"
STATEFILE = "state.json"


# Introspection when running in docker
def get_self_container_id() -> Optional[str]:
    cpuset = Path("/proc/1/cpuset").read_text().strip()
    if cpuset.startswith("/docker/"):
        _, _, container_id = cpuset.split("/")
        return container_id
    else:
        return None


def get_image_name_from_container_id(container_id: str) -> str:
    data = json.loads(subprocess.check_output(["docker", "inspect", container_id]).decode())
    assert len(data) == 1
    _, image_name = data[0]["Image"].split(":")
    return str(image_name)


def get_external_mount_point(container_id: str, internal_path: str) -> Path:
    data = json.loads(subprocess.check_output(["docker", "inspect", container_id]).decode())
    assert len(data) == 1
    mounts = data[0]["Mounts"]
    for mount in mounts:
        if mount.get("Destination") == internal_path:
            return Path(mount["Source"])
    raise Exception(f"External mount point not found for {internal_path}")


SELF_CONTAINER_ID = get_self_container_id()
TASKRUNNER_IMAGE = get_image_name_from_container_id(SELF_CONTAINER_ID) if SELF_CONTAINER_ID else "minimalci:latest"
EXTERNAL_DATA_MOUNT_POINT = get_external_mount_point(SELF_CONTAINER_ID, str(DATA_PATH.absolute())) if SELF_CONTAINER_ID else DATA_PATH.absolute()
EXTERNAL_SSH_MOUNT_POINT = get_external_mount_point(SELF_CONTAINER_ID, str(Path("~/.ssh").expanduser())) if SELF_CONTAINER_ID else Path("~/.ssh").expanduser()
