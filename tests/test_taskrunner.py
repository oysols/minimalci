import sys
sys.path.append(".")

from minimalci.tasks import Task, State
from minimalci.executors import Stash

order = []

class A(Task):
    def run(self) -> None:
        order.append("A")

class B(Task):
    run_after = [A]
    def run(self) -> None:
        order.append("B")

class C(Task):
    run_after = [B]
    def run(self) -> None:
        order.append("C")

class D(Task):
    run_after = [C]
    def run(self) -> None:
        order.append("-")

class E(Task):
    run_after = [D]
    def run(self) -> None:
        order.append("-")

class F(Task):
    run_after = [C]
    def run(self) -> None:
        order.append("-")

class G(Task):
    run_after = [C]
    def run(self) -> None:
        order.append("-")

class H(Task):
    run_after = [D, E, F, G]
    def run(self) -> None:
        order.append("H")

if __name__ == "__main__":
    import __main__  # type: ignore
    from minimalci import taskrunner, tasks
    state = State(Stash(), Stash())
    taskrunner.run_tasks(taskrunner.get_tasks_from_module(__main__), state)
    print(order)
    truth = ['A', 'B', 'C', '-', '-', '-', '-', 'H']
    print(truth)
    assert order == truth
    print("success")
