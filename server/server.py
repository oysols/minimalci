#!/usr/bin/env python3
import subprocess
import os
import logging
import sys
import string
from typing import List, Dict, Tuple, Type
import importlib
import inspect
from multiprocessing import Process
import importlib.util
import threading
from pathlib import Path
import time
import shutil

from minimalci import taskrunner
from minimalci import tasks
from minimalci import executors
import frontend
import config


def git_fetch(repo_path: Path) -> None:
    logging.info("Fetching updates from remote")
    executors.run_command(["git", "-C", str(repo_path), "fetch", "-v"], print_prefix="git  ")


def get_all_branches(repo_path: Path) -> List[Tuple[str, str]]:
    lines = subprocess.check_output(["git", "-C", str(repo_path), "show-ref"]).decode().splitlines()
    branches = []
    for line in lines:
        sha, ref = line.split()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix) and not ref.endswith("HEAD"):
            branch_name = ref.replace(prefix, "")
            branches.append((branch_name, sha))
    return branches


def get_built_shas() -> List[str]:
    return [path.name for path in config.LOGS_PATH.iterdir()]


def get_new_branches() -> List[Tuple[str, str]]:
    built_shas = get_built_shas()
    branches_to_run = []
    logging.info("Checking for changes")
    for branch, new_sha in get_all_branches(config.REPO_PATH):
        if new_sha in built_shas:
            logging.info("No change: {} [{}]".format(branch, new_sha))
        else:
            logging.info("New sha: {} [{}]".format(branch, new_sha))
            branches_to_run.append((branch, new_sha))
    return branches_to_run


def stash_source(repo_path: Path, git_sha: str, output_path: Path) -> Path:
    stash_path = executors.random_tmp_file_path()
    subprocess.check_call(["git", "-C", str(repo_path), "archive", git_sha, "-o", str(stash_path), "--format", "tar.gz"])
    return stash_path


def checkout_repo(repo_path: Path, output_path: Path, branch: str, git_sha: str) -> None:
    if len(list(output_path.iterdir())) > 0:
        raise Exception("Workdir not empty")
    subprocess.check_call(["cp", str(repo_path / ".git"), str(output_path), "-r"])
    subprocess.check_call(["git", "checkout", branch, "-f"], cwd=output_path)
    subprocess.check_call(["git", "reset", "--hard", "origin/" + branch], cwd=output_path)
    sha_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=output_path).decode().strip()
    assert git_sha == sha_head


def safe_name(name: str) -> str:
    legal_chars = string.ascii_letters + string.digits + "-_"
    return "".join([char if char in legal_chars else "_" for char in name])


def build_all_branches() -> None:
    git_fetch(config.REPO_PATH)

    for branch, sha in get_new_branches():
        logging.info("Starting build {} [{}]".format(branch, sha))

        workdir = config.WORK_PATH.absolute() / safe_name(sha)
        logdir = config.LOGS_PATH.absolute() / safe_name(sha)

        if os.path.isdir(logdir):
            shutil.rmtree(logdir)
        os.makedirs(logdir)
        if os.path.isdir(workdir):
            shutil.rmtree(workdir)
        os.makedirs(workdir)

        logging.info(f"Extracting branch to workdir: {workdir}")
        checkout_repo(config.REPO_PATH, workdir, branch, sha)

        source = stash_source(config.REPO_PATH, sha, workdir)
        if list(config.SECRETS_PATH.iterdir()):
            secrets = executors.Local(path=config.SECRETS_PATH).stash("*")
        else:
            secrets = executors.empty_stash()
        state = tasks.State(source, secrets)

        state.commit = sha
        state.branch = branch
        state.repo_name = config.REPO_NAME
        state.log_url = f"{config.BASE_URL}/logs/{sha}"

        state.logdir = logdir

        def task_process(workdir: Path, filename: Path, state: tasks.State) -> None:
            # Set working directory of process
            os.chdir(workdir)
            sys.path.append(str(workdir))
            taskrunner.run_all_tasks_in_file(filename, state)

        process = Process(target=task_process, args=(workdir, config.TASKS_FILE, state))
        process.start()
        process.join()  # TODO: Parallel runs

        # Clean up
        if state.secrets:
            executors.safe_del_tmp_file(state.secrets)
        if state.source:
            executors.safe_del_tmp_file(state.source)
        shutil.rmtree(workdir)


def init() -> None:
    config.LOGS_PATH.mkdir(exist_ok=True)
    if not (config.REPO_PATH / ".git").is_dir():
        subprocess.check_call(["git", "clone", config.REPO_URL, config.REPO_PATH])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init()

    threading.Thread(target=frontend.app.run, kwargs={"host": "0.0.0.0", "port": 8000, "threaded": True}, daemon=True).start()

    build_all_branches()
    while True:
        frontend.TRIGGER.wait()
        frontend.TRIGGER.clear()
        build_all_branches()
