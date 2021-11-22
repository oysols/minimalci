# MinimalCI

Fast and simple continuous integration with Python as DSL.

- Server and frontend `server/`

http://35.227.107.72/

- Executors `executors.py`

```python
with LocalContainer("debian:buster") as e:
    e.sh("echo Hello World > hello_world.txt")
    my_stash = e.stash("hello_world.txt")

with Ssh("user@remotehost") as e:
    e.unstash(my_stash)
    e.sh("cat hello_world.txt")

with Local() as e:
    e.unstash(my_stash)
```

- Semaphore queue `semaphore.py` / `semaphore_subprocess.py`

```python
with semaphore_queue("user@remote_host:path/to/semaphore.queue"):
    do_stuff_while_holding_semaphore()
```

- Tasks `tasks.py`

```python
class Setup(Task):
    def run(self):
        with Local() as e:
            e.sh("some stuff")

class Test(Task):
    run_after = [Setup]
    def run(self):
        with LocalContainer("some_image") as e:
            e.sh(f"echo Testing commit {self.state.commit}")
```

- Taskrunner `taskrunner.py`

```python
# Create a simple State object
state = State()

# or create a State object with additional context for the tasks
state = State(
    commit="1234ca3d41a23d4e4f923",
    branch="some/branch",
    repo_name="MyRepo",
)

# Run Tasks directly
run_tasks([Task1, Task2], state)

# or run Tasks from a separate file
run_all_tasks_in_file("tasks.py", state)
```

# Docs

TODO

## High level overview

### The web server `server.py`

When the server is triggered with a webhook or by manually clicking scan the server will fetch git data from the remote.
If the current branch and commit combinations does not exist in the logs the server will start a new run for each new branch and commit combination.
The server starts a run by:
 - starting a new docker container
 - copying in the `.git` folder to the working directory
 - checking out the correct branch/commit
 - running taskrunner.py

### The taskrunner `taskrunner.py`

The taskrunner
 - collects all Tasks
 - adds a reference to each Task in the State object
 - mocks the `print` function to log all data to the logfile with a task name prefix
 - starts all `Task._run()` at the same time, in separate threads, with the same working directory

### Each task `tasks.py`

The execution of the code in `Task.run()` is managed by `Task._run()`:
 - waits for dependent tasks based on `self.run_after`
 - sets status `Skipped` and skips execution if not all of the dependent tasks have status `Success`, unless `self.run_always`
 - waits for optional semaphores defined in `self.aquire_semaphore`
 - sets status `Running`
 - executes the code in `Task.run()`
 - sets status `Success` on completion, `Failed` if there were any exceptions or `Skipped` if the Skipped exception is raised

# Server setup

- Modify docker-compose.yml according to requirements
- Optional: Add github oauth client id and secret
- Start server with `docker-compose up -d`
- Authorize auto-generated ssh public key (found in `docker-compose logs` and on disk)
- Optional: Register web-hook `/trigger` for triggering repository scan
- Access web interface and check logs to verify setup

# TODO

## Minimalci
- Log jsonlines to stdout?
- Fix print prefix for running executors locally
- Test and verify remote python execution
- Only optionally write log/state
- Set remote env on init of ssh executor


## Server
- Write server logs to web view (git fetch/clone, import, ...)
- Live update overview
- Evaluate polling vs inotify
- state as magic global import?
- Timeouts


## Alternative syntax?

```python
import state

@task(run_after=setup)
def test():
    with Local() as e:
        e.sh(f"echo '{state.commit}'")

    if state.tasks[setup].status == Status.success:
        print("SUCCESS")
```

# License

Copyright (C) 2020 Øystein Olsen

Licensed under GPL-3.0-only
