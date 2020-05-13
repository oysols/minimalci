import uuid
from typing import Tuple
from dataclasses import dataclass
import base64
import json
import urllib.request

@dataclass
class OauthServer:
    authorization_url: str
    access_token_url: str
    user_info_url: str
    client_id: str
    client_secret: str


def begin_oauth(oauth_server: OauthServer) -> Tuple[str, str]:
    session_state = str(uuid.uuid4())
    authorization_url = oauth_server.authorization_url + f"?client_id={oauth_server.client_id}&state={session_state}"
    return authorization_url, session_state


def finish_oauth(oauth_server: OauthServer, request_code: str, request_state: str, session_state: str) -> str:
    if not request_state or request_state != session_state:
        raise Exception("CSRF Error: state mismatch")
    basic_auth = base64.b64encode(":".join([oauth_server.client_id, oauth_server.client_secret]).encode()).decode()
    request = urllib.request.Request(
        oauth_server.access_token_url + f"?code={request_code}",
        headers={"Authorization": f"Basic {basic_auth}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request) as f:
        response = json.loads(f.read())
    access_token = response.get("access_token")
    if access_token is None:
        raise Exception(f"No access token in response: {response}")
    return str(access_token)


def get_username(oauth_server: OauthServer, access_token: str) -> str:
    request = urllib.request.Request(
        oauth_server.user_info_url,
        headers={"Authorization": f"Token {access_token}"},
    )
    with urllib.request.urlopen(request) as f:
        response = json.loads(f.read())
    username = response.get("login")
    if username is None:
        raise Exception(f"No login in response: {response.json()}")
    return str(username)
