from pathlib import Path
import os
import subprocess

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
REPO_PATH = Path("./repo")
LOGS_PATH = Path("./logs")
SECRETS_PATH = Path("./secrets")
WORK_PATH = Path("./workspaces")
LOGFILE = "output.log"
STATEFILE = "taskstate.json"
