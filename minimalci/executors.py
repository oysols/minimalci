from typing import Optional, Any, Type, List, TypeVar, Callable
import queue
from types import TracebackType, FunctionType, FrameType
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
import functools
import time
from signal import SIGKILL, SIGTERM


SENSORED = "********"


global_kill_signal = threading.Event()


def global_kill_signal_handler(signum: int, frame: FrameType) -> None:
    global_kill_signal.set()


class ProcessError(Exception):
    def __init__(self, message: str, stdout: Optional[bytes] = None, stderr: Optional[bytes] = None, exit_code: Optional[int] = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
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


class Stash:
    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            self.path = self._empty_tar()
        else:
            self.path = path

    def __str__(self) -> str:
        return str(self.path)

    @staticmethod
    def _empty_tar() -> Path:
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

    def read_bytes(self, specific_file: str) -> bytes:
        command = [
            "tar",
            "--extract",
            "--gzip",
            "--file", quote(str(self.path)),
            "--to-stdout",
            quote(specific_file),
        ]
        return subprocess.check_output(command)

    def read_text(self, specific_file: str) -> str:
        return self.read_bytes(specific_file).decode().strip()


# Printing


def print_color(color: int, text: str) -> None:
    print(f"\u001b[3{color}m{text}\033[0m")


print_red = functools.partial(print_color, 1)
print_green = functools.partial(print_color, 2)
print_yellow = functools.partial(print_color, 3)


def print_command(command: str, print_prefix: str = "", censor: List[str] = []) -> None:
    for item in censor:
        command = command.replace(item, SENSORED)
    for i, line in enumerate(command.strip().splitlines()):
        indent = "+ " if i == 0 else "  "
        print_yellow(f"{print_prefix}{indent}{line.strip()}")


def print_output(line: str, print_prefix: str) -> None:
    print(f"{print_prefix}{line}")


# Raw shells


def local_shell(command: str, path: Path = Path(), print_prefix: str = "", censor: List[str] = [], **kwargs: Any) -> bytes:
    full_command = ["/bin/bash", "-ce", "cd {} && /bin/bash -ce {}".format(
        quote(str(path)),
        quote(command)
    )]
    print_command(command, print_prefix, censor)
    return run_command(full_command, print_prefix=print_prefix, censor=censor, **kwargs)


def ssh_shell(host: str, command: str, path: Path = Path(), print_prefix: str = "", censor: List[str] = [], **kwargs: Any) -> bytes:
    full_command = ["ssh", host, "cd {} && /bin/bash -ce {}".format(
        quote(str(path)),
        quote(command),
    )]
    print_command(command, print_prefix, censor)
    return run_command(full_command, print_prefix=print_prefix, censor=censor, **kwargs)


# Process control


def stream_handler(
    stream: io.BytesIO,
    should_print: bool = True,
    print_prefix: str = "",
    censor: List[str] = [],
    output_queue: "Optional[queue.Queue[str]]" = None,
) -> bytes:
    output = b""
    for raw_line in iter(stream.readline, b""):
        output += raw_line
        line = raw_line.decode().rstrip()
        for item in censor:
            line = line.replace(item, SENSORED)
        # Handle output including "\r" such as apt-get by removing
        line = line.replace("\r", "")
        if output_queue:
            output_queue.put(line)
        if should_print:
            print_output(line, print_prefix)
    return output


def kill_thread(
    process: "subprocess.Popen[Any]",
    term_callback: Callable[[], None],
    kill_callback: Callable[[], None],
    kill_signal: threading.Event,
    timeout: Optional[int],
    print_prefix: str,
    delay: int = 0,
) -> None:
    start = time.time()
    while not (kill_signal.is_set() or process.poll() is not None):
        kill_signal.wait(timeout=1)
        if timeout is not None and (time.time() - start) < timeout:
            print_output(f"Process timed out after: {timeout} seconds", print_prefix)
            break
    if process.poll() is None and delay:
        time.sleep(delay)  # Delay kill process
    if process.poll() is None:
        print_output("Killing process with SIGTERM", print_prefix)
        term_callback()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            print_output("Process still running: Killing process with SIGKILL", print_prefix)
            kill_callback()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                print_output("Failed to kill process with SIGKILL", print_prefix)


def run_command(
    command: List[str],
    should_print: bool = True,
    print_prefix: str = "",
    censor: List[str] = [],
    output_queue: "Optional[queue.Queue[str]]" = None,
    kill_signal: Optional[threading.Event] = None,
    timeout: Optional[int] = None,
    **kwargs: Any
) -> bytes:
    signal = kill_signal or global_kill_signal
    if signal.is_set():
        raise ProcessError(f"Process start cancelled")
    # Create a process in a separate process group
    with subprocess.Popen(command, stdout=PIPE, stderr=PIPE, stdin=DEVNULL, preexec_fn=os.setsid, **kwargs) as process:
        # Set thread name to match parent thread for taskrunner output aggregation
        thread_name_prefix = threading.current_thread().name + "-"
        # Start process reaper thread
        # Kill process group
        term_handler = functools.partial(os.killpg, process.pid, SIGTERM)
        kill_handler = functools.partial(os.killpg, process.pid, SIGKILL)
        threading.Thread(
            target=kill_thread,
            args=(process, term_handler, kill_handler, signal, timeout, print_prefix),
            daemon=True,
            name=thread_name_prefix + secrets.token_hex(4)
        ).start()
        # Wait for stream handlers to complete to guarantee all output is processed before process is killed
        with concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix=thread_name_prefix) as e:
            stdout = e.submit(stream_handler, process.stdout, should_print, print_prefix, censor, output_queue)
            stderr = e.submit(stream_handler, process.stderr, should_print, print_prefix, censor)
    if process.returncode != 0:
        raise ProcessError(f"Exit code: {process.returncode}", stdout.result(), stderr.result(), process.returncode)
    return stdout.result()


def run_docker_exec_command(
    command: str,
    container_name: str,
    options: List[str] = [],
    print_prefix: str = "",
    censor: List[str] = [],
    output_queue: "Optional[queue.Queue[str]]" = None,
    kill_signal: Optional[threading.Event] = None,
    timeout: Optional[int] = None,
    **kwargs: Any
) -> bytes:
    """Run commands inside container with docker exec

    This is a hack to send termination signals to processes started with docker exec.

    When sending sigterm to the `docker exec` command, the `docker exec` process terminates,
    but it does not forward the signal to the process running in the container.
    This results in the process running indefinitely if the container keeps running,
    or results in the process being killed with sigkill at container termination.

    Being killed with sigkill will not allow the program to perform clean up actions.

    The most noteable example of this is running processes that spawn temporary docker
    containers of their own, since they will not get run time to remove those containers.

    https://github.com/moby/moby/issues/9098
    https://github.com/moby/moby/pull/38704
    """
    # TODO: Delete this function when `docker exec` signal handling is fixed
    full_command = ["docker", "exec"] + options + [container_name]
    full_command += ["/bin/bash", "-ce", "echo MAGICSTRING $$\n" + command]

    signal = kill_signal or global_kill_signal
    if signal.is_set():
        raise ProcessError(f"Process start cancelled")
    with subprocess.Popen(full_command, stdout=PIPE, stderr=PIPE, stdin=DEVNULL, **kwargs) as process:
        # Set thread name to match parent thread for taskrunner output aggregation
        thread_name_prefix = threading.current_thread().name + "-"

        # Last resort kill docker exec process itself
        threading.Thread(
            target=kill_thread,
            args=(process, process.terminate, process.kill, signal, timeout, print_prefix),
            kwargs={"delay": 25},  # Wait until we try the docker exec kill path
            daemon=True,
            name=thread_name_prefix + secrets.token_hex(4),
        ).start()

        # Extract process PID from stdout
        assert process.stdout  # make mypy happy
        first_line = process.stdout.readline().decode()
        try:
            magic_string, raw_pid = first_line.split()
            assert magic_string == "MAGICSTRING"
            pid = int(raw_pid)
        except:
            raise ProcessError(f"Error parsing pid from first line: {first_line}")

        # Wait for stream handlers to complete to guarantee all output is processed before process is killed
        with concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix=thread_name_prefix) as e:
            stdout = e.submit(stream_handler, process.stdout, True, print_prefix, censor, output_queue)
            stderr = e.submit(stream_handler, process.stderr, True, print_prefix, censor)

            # Start process reaper thread
            # Kill process from inside docker container
            # Kill process group to include potential subprocesses
            pgid = -pid  # PGID refers to the process group
            term_handler = functools.partial(
                run_command,
                ["docker", "exec", container_name, "kill", "-SIGTERM", "--", str(pgid)],
                kill_signal=threading.Event(),
            )
            kill_handler = functools.partial(
                run_command,
                ["docker", "exec", container_name, "kill", "-SIGKILL", "--", str(pgid)],
                kill_signal=threading.Event(),
            )
            threading.Thread(
                target=kill_thread,
                args=(process, term_handler, kill_handler, signal, timeout, print_prefix),
                daemon=True,
                name=thread_name_prefix + secrets.token_hex(4)
            ).start()
    if process.returncode != 0:
        raise ProcessError(f"Exit code: {process.returncode}", stdout.result(), stderr.result(), process.returncode)
    return stdout.result()


# Executors


T = TypeVar("T", bound="Executor")


class Executor:
    def __init__(self, path: Optional[Path] = None, temp_path: bool = False):
        if path and temp_path:
            raise Exception("Incompatible arguments")
        self.temp_path = temp_path
        self.path = path or Path()

    def __enter__(self: T) -> T:
        if self.temp_path:
            self.path = self._mk_temp_dir()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        if self.temp_path:
            self._safe_del_tmp_dir(self.path)

    def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
        raise NotImplementedError

    def _tar_to_tmp(self, path_str: str) -> Path:
        stash_path = random_tmp_file_path()
        self.sh("tar --gzip --create --file {} {}".format(
            quote(str(stash_path)),
            quote(path_str),
        ))
        return stash_path

    def _untar_to_cwd(self, tar_path: Path, specific_file: str) -> None:
        command = "tar --extract --gzip --file {}".format(quote(str(tar_path)))
        if specific_file:
            command += " {}".format(quote(specific_file))
        self.sh(command)

    def _safe_del_tmp_file(self, path: Path) -> None:
        assert_path_in_tmp(path)
        self.sh(
            "rm {}".format(quote(str(path))),
            kill_signal=threading.Event(),  # Override global kill signal
        )

    def _safe_del_tmp_dir(self, path: Path) -> None:
        assert_path_in_tmp(path)
        self.sh(
            "rm -r {}".format(quote(str(path))),
            kill_signal=threading.Event(),  # Override global kill signal
        )

    def _mk_temp_dir(self) -> Path:
        temp_dir = random_tmp_file_path()
        self.sh(
            "mkdir {}".format(quote(str(temp_dir))),
            kill_signal=threading.Event(),  # Override global kill signal
        )
        return temp_dir


class Local(Executor):
    def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
        return local_shell(command, path=self.path, censor=censor, **kwargs)

    def stash(self, path: str) -> Stash:
        stash_path = self._tar_to_tmp(path)
        safe_del_tmp_file_atexit(stash_path)
        return Stash(stash_path)

    def stash_from_git_archive(self, commit: str) -> Stash:
        stash_path = random_tmp_file_path()
        self.sh(f"git archive {quote(commit)} -o {quote(str(stash_path))} --format tar.gz")
        safe_del_tmp_file_atexit(stash_path)
        return Stash(stash_path)

    def unstash(self, stash: Stash, specific_file: str = "") -> None:
        self._untar_to_cwd(stash.path, specific_file)


class Ssh(Executor):
    def __init__(self, host: str, **kwargs: Any):
        self.host = host
        super().__init__(**kwargs)

    def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
        return ssh_shell(self.host, command, path=self.path, censor=censor, **kwargs)

    def stash(self, path_glob: str) -> Stash:
        remote_stash_path = self._tar_to_tmp(path_glob)
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
        return Stash(local_stash_path)

    def unstash(self, stash: Stash, specific_file: str = "") -> None:
        tmp_path = random_tmp_file_path()
        command = "scp {} {}".format(
            quote(str(stash)),
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
        self.container_name = "exe_" + secrets.token_hex(16)
        self.print_prefix = "" # TODO: get_print_prefix(self.image)
        super().__init__(**kwargs)

    def __enter__(self) -> "LocalContainer":
        command = "docker run --rm --name {} {} -t -d {} /bin/bash -c cat".format(
            quote(self.container_name),
            "-v /var/run/docker.sock:/var/run/docker.sock" if self.mount_docker else "",
            quote(self.image),
        )
        local_shell(
            command,
            kill_signal=threading.Event(),  # Override global kill signal
        )
        super().__enter__()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        local_shell(
            "docker rm -f {}".format(quote(self.container_name)),
            kill_signal=threading.Event(),  # Override global kill signal
        )


    def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
        print_command(command, self.print_prefix, censor)
        options = [] if self.path == Path() else ["--workdir", quote(str(self.path))]
        return run_docker_exec_command(command, self.container_name, options=options, **kwargs)


    # TODO: Reinstate this function once `docker exec` signaling is fixed
    # def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
    #     print_command(command, self.print_prefix, censor)
    #     workdir = [] if self.path == Path() else ["--workdir", quote(str(self.path))]
    #     full_command = ["docker", "exec"] + workdir + [self.container_name,]
    #     full_command += ["/bin/bash", "-ce", command]
    #     return run_command(full_command, print_prefix=self.print_prefix, censor=censor, **kwargs)


    def chown_file_to_docker_user(self, container_path: Path) -> bytes:
        docker_user = self.sh("whoami").decode().strip()
        command = "chown {0}:{0} {1}".format(
            quote(docker_user),
            quote(str(container_path)),
        )
        print_command(command, self.print_prefix)
        full_command = ["docker", "exec", "--user", "root", self.container_name, "/bin/bash", "-ce", command]
        return run_command(full_command, print_prefix=self.print_prefix)

    def stash(self, path_glob: str) -> Stash:
        container_stash_path = self._tar_to_tmp(path_glob)
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
        return Stash(local_stash_path)

    def unstash(self, stash: Stash, specific_file: str = "") -> None:
        container_tmp_path = random_tmp_file_path()
        command = "docker cp {} {}:{}".format(
            quote(str(stash)),
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
        print_command(command_str, self.print_prefix)
        self.process = subprocess.Popen(command, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        for raw_line in iter(self.process.stdout.readline, b""):  # type: ignore
            # Loop forever
            line = raw_line.decode().rstrip()
            if line == "ready":
                break
        else:
            for raw_line in iter(self.process.stderr.readline, b""):  # type: ignore
                line = raw_line.decode().rstrip()
                print_output(line, self.print_prefix)
            raise Exception("Forwarding failed")
        super().__enter__()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
        self.process.terminate()
        self.process.wait()
        self._safe_del_tmp_file(self.forwarded_socket)
        super().__exit__(exc_type, exc_value, traceback)

    def sh(self, command: str, censor: List[str] = [], **kwargs: Any) -> bytes:
        env = os.environ.copy()
        env["DOCKER_HOST"] = "unix://{}".format(self.forwarded_socket)
        return local_shell(
                command,
                path=self.path,
                print_prefix=self.print_prefix,
                censor=censor,
                env=env,
        )
