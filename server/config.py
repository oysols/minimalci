from pathlib import Path
import os

# Required
REPO_URL = os.environ.get("REPO_URL", ".")
REPO_NAME = os.environ.get("REPO_NAME", "testing")
BASE_URL = os.environ.get("BASE_URL", "http://localhost")

# Optional
TASKS_FILE = Path(os.environ.get("TASKS_FILE", "tasks.py"))
DOCKER_REGISTRY = os.environ.get("DOCKER_REGISTRY", "")
if DOCKER_REGISTRY:  # Docker registry auto login at import time
    pass
DOCKER_USER = ""
DOCKER_PASS = ""

# Static
REPO_PATH = Path("./repo")
LOGS_PATH = Path("./logs")
SECRETS_PATH = Path("./secrets")
WORK_PATH = Path("./workspaces")
LOGFILE = "output.log"
STATEFILE = "taskstate.json"
