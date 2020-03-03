#!/usr/bin/env python3
"""Forward remote docker.sock and set DOCKER_HOST in subshell"""
import os
import sys
import subprocess
import argparse
from typing import Dict

from minimalci import executors

# Prompt colors
RED = "\033[91m"
END_COLOR = "\033[0m"


def start_subshell(env: Dict[str, str], prompt_info: str) -> None:
    """Start an interactive subshell with custom env and prompt"""
    cmd = os.environ.get("SHELL", "/bin/sh")
    # PS1 sets the prompt for the subshell for visual display of remote docker host
    env["PS1"] = "[{}]:\\w\\$ ".format(prompt_info)
    process = subprocess.Popen([cmd, "--norc"], env=env)
    process.wait()


def main() -> None:
    """Forward remote docker.sock and set DOCKER_HOST in subshell"""
    parser = argparse.ArgumentParser(description="Forward remote docker.sock and set DOCKER_HOST in subshell")
    parser.add_argument("remote_host", help="Remote host")
    parser.add_argument("command", nargs="*", help="Run a single command")
    args = parser.parse_args()

    remote_host = args.remote_host
    command = args.command

    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        print("ERROR: DOCKER_HOST already set: {}".format(docker_host))
        sys.exit(1)
    with executors.LocalWithForwardedDockerSock(remote_host) as exe:
        if command:
            exe.sh(command)
        else:
            # prompt_info = "DOCKER_HOST -> {}{}{}".format(RED, remote_host, END_COLOR)
            prompt_info = remote_host
            env = os.environ.copy()
            env["DOCKER_HOST"] = "unix://{}".format(exe.forwarded_socket)
            start_subshell(env, prompt_info)


if __name__ == "__main__":
    main()
