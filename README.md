# MinimalCI

Fast and simple continuous integration wiht Python as DSL.

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

- Tasks / Taskrunner `tasks.py` / `taskrunner.py`

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

# Docs

TODO

## Server setup

- SSH setup

```
ssh-keygen -P '' -f /srv/minimalci/ssh/id_rsa
ssh-keyscan -t rsa github.com >> /srv/minimalci/ssh/known_hosts
```

# TODO

## Minimalci
- Fix print prefix for running executors locally
- Run taskrunner in docker
- Setup consistent executor queues without relying on local semaphore
- Test and verify remote python execution
- Only optionally write log/state

## Server
- Authentication
- Stop, Delete, Trigger from web UI
- Do not print task logs to server logs
- Write server logs to web view (git fetch/clone, import, ...)
- Show current git tags in overview
- Live update overview
- Evaluate polling vs inotify
- "Dependent task did not succeed" when raising Skipped. Handling by user instead?
- state as magic global import?
- Run taskrunner from webserver, not the other way around?
- Visualize dependent tasks by using indentation
- Detect crashed builds
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
