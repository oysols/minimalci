import json
import urllib.request
import enum


class GithubState(enum.Enum):
    success = "success"
    failure = "failure"
    pending = "pending"
    error = "error"


def set_github_status(state: GithubState, repo: str, sha: str, context: str, target_url: str, github_auth: str) -> None:
    data = {
        "state": state.value,  # success, failure, pending, error
        "target_url": target_url,
        "description": state.value,
        "context": context,
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/statuses/{sha}",
        data=json.dumps(data).encode(),
        headers={"Authorization": f"token {github_auth}"},
    )
    res = urllib.request.urlopen(request)
