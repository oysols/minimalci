from pathlib import Path

from minimalci.executors import LocalContainer, Local
from minimalci.tasks import Task, Status


image_name: str
source: Path


class Setup(Task):
    def run(self) -> None:
        with Local() as exe:
            global source
            source = exe.stash("*")
            global image_name
            image_name = f"test:{self.state.commit}"
            exe.sh(f"docker build . -t {image_name}")
            #exe.unstash(self.state.secrets, "supersecret.txt")


class Test(Task):
    run_after = [Setup]
    def run(self) -> None:
        with LocalContainer(image_name, temp_path=True, mount_docker=True) as exe:
            print(f"Testing commit {self.state.commit}")
            exe.unstash(source)
            exe.sh("make test")


class Lint(Task):
    run_after = [Test]
    def run(self) -> None:
        with LocalContainer(image_name, temp_path=True) as exe:
            exe.unstash(source)
            exe.sh("make check")


class ErrorHandler(Task):
    run_after = [Test, Lint]
    run_always = True
    def run(self) -> None:
        with Local() as exe:
            for task in self.state.tasks:
                print(f"{task.name} {task.status.name}")


if __name__ == "__main__":
    import __main__  # type: ignore
    from minimalci import taskrunner, tasks

    all_tasks = taskrunner.get_tasks_from_module(__main__)
    state = tasks.State()
    state.commit = "localtest"
    taskrunner.run_tasks(all_tasks, state)
