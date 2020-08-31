from minimalci.executors import LocalContainer, Local, Stash
from minimalci.tasks import Task, Status
from minimalci import task_util


image_name: str
source: Stash

# github_auth = Path("/secrets/github_auth.txt").read_text().strip()


class Setup(Task):
    def run(self) -> None:
        with Local() as exe:
            global source, image_name
            source = exe.stash(".")
            image_name = f"minimalci:{self.state.commit}"
            exe.sh(f"docker build . -t {image_name}")
            exe.sh(f"echo secretstuff", censor=["secretstuff"])


class Test(Task):
    run_after = [Setup]

    def run(self) -> None:
        with LocalContainer(image_name, temp_path=True, mount_docker=True) as exe:
            exe.unstash(source)
            exe.sh("make test")


class Lint(Task):
    run_after = [Setup]

    def run(self) -> None:
        with LocalContainer(image_name, temp_path=True) as exe:
            exe.unstash(source)
            exe.sh("make check")


class ErrorHandler(Task):
    run_after = [Test, Lint]
    run_always = True

    def run(self) -> None:
        if all(task.status == Status.success for task in self.state.tasks if task is not self):
            print("Set github status", task_util.GithubState.success.name)
        else:
            print("Set github status", task_util.GithubState.failure.name)
