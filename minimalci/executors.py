from typing import Optional, Any, Type, List, TypeVar
import queue
from types import TracebackType, FunctionType
import threading
import subprocess
from subprocess import PIPE, DEVNULL
import io
import concurrent.futures
from shlex import quote
import os
import atexit
import secrets
from pathlib import Path


T = TypeVar("T")


class NonZeroExit(Exception):
    def __init__(self, message: str, stdout: bytes, stderr: bytes):
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


# Utils

# TODO: Improve printing of forwarded hosts/containers
# def get_print_prefix(mainhost: str, subhost: str = "") -> str:
#     return "{:<15} ".format(mainhost if not subhost else "{}[{}]".format(mainhost, subhost))


def random_tmp_file_path() -> Path:
    return Path("/tmp") / f"exe_{secrets.token_hex(16)}"


def assert_path_in_tmp(path: Path) -> None:
    if not path.is_absolute():
        raise Exception(f"Temp path is not absolute: {path}")
    if not path.parents[0] == Path("/tmp"):
        raise Exception("Temp path does not start with '/tmp/': {}".format(path))


def safe_del_tmp_file(full_path: Path) -> None:
    assert_path_in_tmp(full_path)
    os.unlink(full_path)


def safe_del_tmp_file_atexit(full_path: Path) -> None:
    assert_path_in_tmp(full_path)
    atexit.register(safe_del_tmp_file, full_path)


def bytes_from_stash(tar_path: Path, specific_file: str) -> bytes:
    command = [
        "tar",
        "--extract",
        "--gzip",
        "--file",
        quote(str(tar_path)),
        quote(specific_file),
        "--to-stdout",
    ]
    return run_command(command)


def text_from_stash(tar_path: Path, specific_file: str) -> str:
    return bytes_from_stash(tar_path, specific_file).decode().strip()


def empty_stash() -> Path:
    tmp_path = random_tmp_file_path()
    command = [
        "tar",
        "--create",
        "--gzip",
        "--file", str(tmp_path),
        "--files-from", "/dev/null",
    ]
    run_command(command)
    return tmp_path


# Raw shells


def local_shell(command: str, path: Path = Path(), print_prefix: str = "", **kwargs: Any) -> bytes:
    full_command = ["/bin/bash", "-ce", "cd {} && /bin/bash -ce {}".format(
        quote(str(path)),
        quote(command)
    )]
    print(f"{print_prefix}+ {command}")
    return run_command(full_command, print_prefix=print_prefix, **kwargs)


def ssh_shell(host: str, command: str, path: Path = Path(), print_prefix: str = "", **kwargs: Any) -> bytes:
    full_command = ["ssh", host, "cd {} && /bin/bash -ce {}".format(
        quote(str(path)),
        quote(command),
    )]
    print(f"{print_prefix}+ {command}")
    return run_command(full_command, print_prefix=print_prefix, **kwargs)


# Process control


def stream_handler(stream: io.BytesIO, print_prefix: str = "", output_queue: "Optional[queue.Queue[str]]" = None) -> bytes:
    output = b""
    for raw_line in iter(stream.readline, b""):
        output += raw_line
        line = raw_line.decode().rstrip()
        # Handle output including "\r" such as apt-get by removing
        line = line.replace("\r", "")
        if output_queue:
            output_queue.put(line)
        else:
            print(print_prefix + line)
    return output


def kill_thread(process: "subprocess.Popen[Any]", kill_signal: threading.Event) -> None:
    while not (kill_signal.is_set() or process.poll() is not None):
        kill_signal.wait(timeout=5)
    if process.poll() is None:
        msg = "Killing process with SIGTERM"
        print(msg)
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            msg = "Process still running: Killing process with SIGKILL"
            print(msg)
            process.kill()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                msg = "Failed to kill process with SIGKILL"
                print(msg)


def run_command(
    command: List[str],
    print_prefix: str = "",
    output_queue: "Optional[queue.Queue[str]]" = None,
    kill_signal: Optional[threading.Event] = None,
    **kwargs: Any
) -> bytes:
    with subprocess.Popen(command, stdout=PIPE, stderr=PIPE, stdin=DEVNULL, **kwargs) as process:
        # Set thread name to match parent thread for taskrunner output aggregation
        with concurrent.futures.ThreadPoolExecutor(3, thread_name_prefix=threading.current_thread().name + "-") as e:
            stdout = e.submit(stream_handler, process.stdout, print_prefix, output_queue)
            stderr = e.submit(stream_handler, process.stderr, print_prefix)
            if kill_signal:
                reaper = e.submit(kill_thread, process, kill_signal)
    if process.returncode != 0:
        raise NonZeroExit(f"Exit code: {process.returncode}", stdout.result(), stderr.result())
    return stdout.result()


# Executors


class Executor:
    def __init__(self, path: Optional[Path] = None, temp_path: bool = False):
        if path and temp_path:
            raise Exception("Incompatible arguments")
        self.temp_path = temp_path
        self.path = Path()
        if path:
            self.path = path
        elif temp_path:
            self.path = self._mk_temp_dir()

    def __enter__(self: T) -> T:
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        if self.temp_path:
            self._safe_del_tmp_dir(self.path)

    def sh(self, command: str) -> bytes:
        raise NotImplementedError

    def _tar_to_tmp(self, path: str) -> Path:
        stash_path = random_tmp_file_path()
        self.sh("tar --gzip --create --file {} {}".format(
            quote(str(stash_path)),
            path,  # TODO: unsafe. fix? Not escaped to deal with globbing/pathname expansion
        ))
        return stash_path

    def _untar_to_cwd(self, tar_path: Path, specific_file: str) -> None:
        self.sh("tar --extract --gzip --file {} {}".format(
            quote(str(tar_path)),
            quote(specific_file),
        ))

    def _safe_del_tmp_file(self, path: Path) -> None:
        assert_path_in_tmp(path)
        self.sh("rm {}".format(
            quote(str(path)),
        ))

    def _safe_del_tmp_dir(self, path: Path) -> None:
        assert_path_in_tmp(path)
        self.sh("rm -r {}".format(
            quote(str(path)),
        ))

    def _mk_temp_dir(self) -> Path:
        temp_dir = random_tmp_file_path()
        self.sh("mkdir {}".format(
            quote(str(temp_dir)),
        ))
        return temp_dir


class Local(Executor):
    def sh(self, command: str, **kwargs: Any) -> bytes:
        return local_shell(command, path=self.path, **kwargs)

    def stash(self, path: str) -> Path:
        stash_path = self._tar_to_tmp(path)
        safe_del_tmp_file_atexit(stash_path)
        return stash_path

    def unstash(self, local_path: Path, specific_file: str = "") -> None:
        self._untar_to_cwd(local_path, specific_file)


class Ssh(Executor):
    def __init__(self, host: str, **kwargs: Any):
        self.host = host
        super().__init__(**kwargs)

    def sh(self, command: str, **kwargs: Any) -> bytes:
        return ssh_shell(self.host, command, path=self.path, **kwargs)

    def stash(self, path: str) -> Path:
        remote_stash_path = self._tar_to_tmp(path)
        try:
            local_stash_path = random_tmp_file_path()
            command = "scp {} {}".format(
                quote("{}:{}".format(self.host, remote_stash_path)),
                quote(str(local_stash_path)),
            )
            local_shell(command)
        finally:
            self._safe_del_tmp_file(remote_stash_path)
        safe_del_tmp_file_atexit(local_stash_path)
        return local_stash_path

    def unstash(self, local_path: Path, specific_file: str = "") -> None:
        tmp_path = random_tmp_file_path()
        command = "scp {} {}".format(
            quote(str(local_path)),
            quote("{}:{}".format(self.host, tmp_path)),
        )
        local_shell(command)
        try:
            self._untar_to_cwd(tmp_path, specific_file)
        finally:
            self._safe_del_tmp_file(tmp_path)


class LocalContainer(Executor):
    # TODO: Evaluate possible user permission conflicts
    def __init__(self, image: str = "debian", mount_docker: bool=False, **kwargs: Any):
        self.image = image
        self.mount_docker = mount_docker
        self.container_name = secrets.token_hex(16)
        self.print_prefix = "" # TODO: get_print_prefix(self.image)
        command = "docker run --rm --name {} {} -t -d {} /bin/bash -c cat".format(
            quote(self.container_name),
            "-v /var/run/docker.sock:/var/run/docker.sock" if self.mount_docker else "",
            quote(self.image),
        )
        local_shell(command)
        super().__init__(**kwargs)

    def __enter__(self) -> "LocalContainer":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        local_shell("docker rm -f {}".format(quote(self.container_name)))

    def sh(self, command: str) -> bytes:
        for line in command.splitlines():
            print(f"+ {line}")
        workdir = [] if self.path == Path() else ["--workdir", quote(str(self.path))]
        full_command = ["docker", "exec"] + workdir + ["-t", self.container_name, "/bin/bash", "-ce", command]
        return run_command(full_command, print_prefix=self.print_prefix)

    def chown_file_to_docker_user(self, container_path: Path) -> bytes:
        docker_user = self.sh("whoami").decode().strip()
        command = "chown {0}:{0} {1}".format(
            quote(docker_user),
            quote(str(container_path)),
        )
        print(f"+ {command}")
        full_command = ["docker", "exec", "--user", "root", self.container_name, "/bin/bash", "-ce", command]
        return run_command(full_command, print_prefix=self.print_prefix)

    def stash(self, path: str) -> Path:
        container_stash_path = self._tar_to_tmp(path)
        try:
            local_stash_path = random_tmp_file_path()
            command = "docker cp {}:{} {}".format(
                quote(self.container_name),
                quote(str(container_stash_path)),
                quote(str(local_stash_path)),
            )
            local_shell(command)
        finally:
            self._safe_del_tmp_file(container_stash_path)
        safe_del_tmp_file_atexit(local_stash_path)
        return local_stash_path

    def unstash(self, local_stash_path: Path, specific_file: str = "") -> None:
        container_tmp_path = random_tmp_file_path()
        command = "docker cp {} {}:{}".format(
            quote(str(local_stash_path)),
            quote(self.container_name),
            quote(str(container_tmp_path)),
        )
        local_shell(command)
        self.chown_file_to_docker_user(container_tmp_path)
        try:
            self._untar_to_cwd(container_tmp_path, specific_file)
        finally:
            self._safe_del_tmp_file(container_tmp_path)

    def call_func(self, func: FunctionType, *args: Any, **kwargs: Any) -> Any:
        # TODO: Make this general
        import inspect
        import json
        import base64
        args_json_b64 = base64.b64encode(json.dumps({"args": args, "kwargs": kwargs}).encode()).decode()
        function_source = inspect.getsource(func),
        data = [
            "import json, base64",
            "__args_json_b64 = '{}'".format(args_json_b64),
            "__arguments = json.loads(base64.b64decode(__args_json_b64.encode()).decode())",
            "\n".join(function_source),
            "__results = {}(*__arguments['args'], **__arguments['kwargs'])".format(func.__name__),
            "print()",
            "print(json.dumps(__results))",
        ]
        data_string = "\n".join(data)
        full_command = ["docker", "exec", "-t", self.container_name, "bash", "-ce", "python3 -uc {}".format(quote(data_string))]
        print(full_command)
        output = run_command(full_command, print_prefix=self.print_prefix)
        last_line = output.decode().splitlines()[-1]
        try:
            return json.loads(last_line)
        except Exception:
            raise Exception("Failed to parse results json:\n\n{}".format(str(output)))


class LocalWithForwardedDockerSock(Local):
    def __init__(self, host: str, **kwargs: Any):
        self.host = host
        path = random_tmp_file_path()
        self.forwarded_socket = path.parent / (path.name + ".sock")
        self.print_prefix = ""  # TODO: get_print_prefix("local", self.host)
        super().__init__(**kwargs)

    def __enter__(self) -> "LocalWithForwardedDockerSock":
        command = [
                "ssh", "-tt",
                "-L", "{}:/var/run/docker.sock".format(self.forwarded_socket),
                # TODO: LocalWithForwardedPort
                # "-L", "localhost:{}:localhost:{}".format(self.local_port, self.remote_port),
                "-o", "PasswordAuthentication no",
                self.host,
                "echo 'ready' && cat"
        ]
        command_str = " ".join(command)
        print(f"{self.print_prefix}+ {command_str}")
        self.process = subprocess.Popen(command, stdout=PIPE, stderr=PIPE, stdin=PIPE, bufsize=1)
        for raw_line in iter(self.process.stdout.readline, b""):  # type: ignore
            # Loop forever
            line = raw_line.decode().rstrip()
            if line == "ready":
                break
        else:
            for raw_line in iter(self.process.stderr.readline, b""):  # type: ignore
                line = raw_line.decode().rstrip()
                print(line)
            raise Exception("Forwarding failed")
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        self.process.terminate()
        self.process.wait()
        self._safe_del_tmp_file(self.forwarded_socket)
        super().__exit__(exc_type, exc_value, traceback)

    def sh(self, command: str, **kwargs: Any) -> bytes:
        env = os.environ.copy()
        env["DOCKER_HOST"] = "unix://{}".format(self.forwarded_socket)
        return local_shell(
                command,
                path=self.path,
                print_prefix=self.print_prefix,
                env=env,
        )
