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

```
with semaphore_queue("user@remote_host:path/to/semaphore.queue"):
    do_stuff_while_holding_semaphore()
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
