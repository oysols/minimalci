import concurrent.futures
import subprocess
import time
import sys
import signal

sys.path.append(".")
from minimalci.executors import Local, LocalContainer, global_kill_signal, ProcessError, Stash, set_sigterm_sigint_global_kill_signal_handler


def test_stash() -> None:
    with Local() as exe:
        source = exe.stash(".")
        ls = exe.sh("ls -l --time-style=+%s Dockerfile").decode()

    with LocalContainer("debian", path="/root") as exe2:
        exe2.unstash(source, "./Dockerfile")
        ls2 = exe2.sh("ls -l --time-style=+%s Dockerfile").decode()
        normalized1 = " ".join(ls.split()[:2] + ls.split()[4:])
        normalized2 = " ".join(ls2.split()[:2] + ls2.split()[4:])
        print(normalized1)
        print(normalized2)
        assert normalized1 == normalized2


def test_docker_in_docker() -> None:
    with Local() as exe:
        exe.sh("docker build . -t test")
    with LocalContainer("test", temp_path=True, mount_docker=True) as exe2:
        exe2.sh("docker ps")


def test_temp_path() -> None:
    with LocalContainer("debian", temp_path=True) as exe:
        assert exe.sh("pwd").decode().strip().startswith("/tmp/")


def test_docker_exec_signal_handling() -> None:
    def run_catch(exe: LocalContainer, catch_signal_stash: Stash) -> None:
        exe.unstash(catch_signal_stash)
        exe.sh("python3 -u tests/catch_signals.py")

    with concurrent.futures.ThreadPoolExecutor(1) as e:
        with Local() as local_exe:
            catch_signal_stash = local_exe.stash("tests/catch_signals.py")

            with LocalContainer("python") as exe:
                f = e.submit(run_catch, exe, catch_signal_stash)

                # Wait until process is running and file exists
                for _ in range(30):
                    try:
                        time.sleep(1)
                        local_exe.sh(f"docker exec {exe.container_name} cat is_sleeping")
                        break
                    except:
                        pass

                global_kill_signal.set()
                print("global_kill_signal set")
                try:
                    f.result()
                except ProcessError as e:
                    assert e.exit_code == 101
                else:
                    raise Exception("Expected exception")
            print("killed container")
    global_kill_signal.clear()


if __name__ == "__main__":
    set_sigterm_sigint_global_kill_signal_handler()
    test_docker_exec_signal_handling()
    test_stash()
    test_temp_path()
    test_docker_in_docker()
