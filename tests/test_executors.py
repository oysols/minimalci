import sys
sys.path.append(".")

from minimalci.executors import Local, LocalContainer


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


if __name__ == "__main__":
    test_stash()
    test_temp_path()
    test_docker_in_docker()
