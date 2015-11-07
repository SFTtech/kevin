"""
Job processing code
"""

import json
import shutil
from threading import Lock
import traceback

from .chantal import Chantal
from .config import CFG
from .util import coroutine, ProcTimeoutError
from .jobupdate import JobUpdate, BuildState, StepState, StdOut, OutputItem


class Job:
    """
    Holds all info for one build job, identified by its commit SHA id.

    The constructor takes the commit id as an argument.
    It checks the current state of the job, and acts accordingly:
     - If the job has been marked as 'completed' in the file system,
       its contents are loaded.
     - If it hasn't, any files for this job are purged from the file system.

    No two job objects with the same job id may exist.
    The JOBS, COMPLETED_JOB_CACHE, and JOB_LOCK objects are to be
    used to ensure that this constraint is met.
    """
    def __init__(self, job_id):
        if not job_id.isalnum() or not len(job_id) == 40:
            raise ValueError("bad commit SHA: " + repr(job_id))
        self.job_id = job_id

        # callbacks that are to be invoked for each job update during the
        # 'building' phase.
        self.watchers = set()

        # URL where the repo containing this commit can be cloned from
        # during the 'building' phase.
        self.clone_url = None

        # List of job status update JSON objects.
        self.updates = []
        self.update_lock = Lock()

        self.path = CFG.web_folder.joinpath(self.job_id)
        self.target_url = CFG.web_url + "?job=" + self.job_id

        # Internal cache, used when erroring all remaining pending states.
        self.pending_steps = set()

        # {step name: step number}, used for step name prefixes
        self.step_numbers = dict()

        # all commited output items
        self.output_items = set()
        # current uncommited output item
        self.current_output_item = None
        # current step name of the job
        self.current_step = None
        # remaining size limit
        self.remaining_output_size = CFG.max_output

        # Check the current status of the job.
        self.completed = self.path.joinpath("_completed").is_file()
        if self.completed:
            # load update list from file
            with self.path.joinpath("_updates").open() as updates_file:
                for json_line in updates_file:
                    self.update(JobUpdate.construct(json_line))
        else:
            # make sure that there are no remains of previous aborted jobs.
            try:
                shutil.rmtree(str(self.path))
            except FileNotFoundError:
                pass

    def watch(self, watcher):
        """
        Registers a watcher object.
        The watcher's new_update() member method will be called for every
        update that ever was and ever will be.

        The JSON-serializable update dict is passed.
        Once the end of updates has come, there will be one last, fatal call,
        passing StopIteration.
        """
        with self.update_lock:
            self.watchers.add(watcher)
            # send all previous updates to the watcher
            for update in self.updates:
                watcher.new_update(update)

            if self.completed:
                watcher.new_update(StopIteration)

    def unwatch(self, watcher):
        """ Un-subscribes a previouly-registered watcher. """
        with self.update_lock:
            self.watchers.remove(watcher)

    def update(self, update):
        """
        Applies an update to self, broadcasts it, appends it to self.updates.

        The argument shall be a JobUpdate object.
        """
        with self.update_lock:
            print("\x1b[33mjob.update\x1b[m: " + repr(update))
            if update != StopIteration:
                # this automatically manages the pending_steps set.
                update.apply_to(self)
                self.updates.append(update)

            for watcher in self.watchers:
                watcher.new_update(update)

            if self.completed and update != StopIteration:
                # append this update to the updates file
                with self.path.joinpath("_updates").open("a") as updates_file:
                    updates_file.write(update.json() + "\n")

    def build(self):
        """
        Attempts to build the job.
        """
        # create the output directory structure
        self.path.mkdir()
        self.update(BuildState("pending", "booting VM"))

        try:
            with Chantal() as chantal:
                chantal.wait_for_ssh_port(timeout=30)
                print("installing chantal via scp")

                # TODO: location detection
                chantal.upload("/home/kevin/kevin/chantal")

                print("running chantal via ssh")
                chantal_output = chantal.run_command(
                    "python3", "-u", "-m",
                    "chantal", self.clone_url, self.job_id, CFG.job_desc_file,
                    timeout=CFG.job_timeout,
                    silence_timeout=CFG.silence_timeout,
                )
                control_handler = self.control_handler()
                for stream_id, data in chantal_output:
                    if stream_id == 1:
                        # stdout message
                        self.update(
                            StdOut(data.decode("utf-8", errors="replace"))
                        )
                    elif stream_id == 2:
                        # control message stream chunk
                        control_handler.send(data)

        except ProcTimeoutError as exc:
            # did it too long to finish?
            if exc.was_global:
                print("\x1b[31;1mJob timeout! Took %.03fs, "
                      "over global limit of %.2fs.\x1b[m" % (
                    exc.timeout,
                    CFG.job_timeout,
                ))

                if self.current_step:
                    self.update(StepState(self.current_step, "error", "Timeout!"))

                self.error("Job took > %.02fs." % (CFG.job_timeout))

            # or too long to provide a message?
            else:
                print("\x1b[31;1mJob silence timeout! Quiet for %.03fs > "
                      "%.2fs.\x1b[m" % (exc.timeout, CFG.silence_timeout))

                # a specific step is responsible:
                if self.current_step:
                    self.update(StepState(self.current_step, "error",
                                          "Silence for > %.02fs." % (
                                              CFG.silence_timeout)))
                    self.error("Silence Timeout!")
                else:
                    # bad step is unknown:
                    self.error("Silence for > %.2fs!" % (CFG.silence_timeout))

        except BaseException as exc:
            print("\x1b[31;1mexception in Job.build()\x1b[m", end=" ")
            print("[\x1b[33m" + self.job_id + "\x1b[m]")
            traceback.print_exc()
            self.error("Job.build(): " + repr(exc))

        finally:
            for step in self.pending_steps:
                self.update(
                    StepState(step, 'error', 'step result was not reported')
                )
            # dump all job updates to the filesystem
            with self.path.joinpath("_updates").open("w") as updates_file:
                with self.update_lock:
                    for update in self.updates:
                        updates_file.write(update.json() + "\n")
            # the job is now officially completed
            self.path.joinpath("_completed").touch()
            with self.update_lock:
                self.completed = True
            self.update(StopIteration)

    def error(self, text):
        """
        Produces an 'error' BuildState and an 'error' StepState for all
        steps that are currently pending.
        """
        self.update(BuildState("error", text))
        while self.pending_steps:
            step = self.pending_steps.pop()
            self.update(StepState(step, "error", "build has errored"))

    @coroutine
    def control_handler(self):
        """
        Coroutine that receives control data chunks via yield, and
        interprets them.
        Note: The data chunks are entirely untrusted.
        """
        data = b""
        raw_file, raw_remaining = None, 0
        while True:
            if raw_file is not None:
                # control stream is in raw binary mode
                if not data:
                    # wait for more data
                    data += yield
                if len(data) < raw_remaining:
                    raw_file.write(data[:raw_remaining])
                    data = data[raw_remaining:]
                    raw_file.close()
                    raw_file = None
                else:
                    raw_file.write(data)
                    raw_remaining -= len(data)
                    data = b""
                continue

            # control stream is in regular JSON+'\n' mode
            newline = data.find(b"\n")
            if newline == -1:
                if len(data) > (8 * 1024 * 1024):
                    # chantal is trying to crash us with a >= 8MiB msg
                    raise ValueError("Control message too long")
                # wait for more data
                data += yield
                continue

            msg, data = data[:newline], data[newline + 1:]
            msg = msg.decode()
            print("control: " + msg)
            msg = json.loads(msg)

            cmd = msg["cmd"]

            if cmd == 'build-state':
                self.update(BuildState(msg["state"], msg["text"]))

            elif cmd == 'step-state':
                self.current_step = msg["step"]
                self.update(StepState(
                    msg["step"],
                    msg["state"],
                    msg["text"]
                ))

            elif cmd == 'output-item':
                name = msg["name"]

                if self.current_output_item is None:
                    raise ValueError("no data received for " + name)
                if self.current_output_item.name != name:
                    raise ValueError(
                        "wrong output item name: " + name + ", "
                        "expected: " + self.current_output_item.name
                    )

                self.update(self.current_output_item)
                self.current_output_item = None

            elif cmd in {'output-dir', 'output-file'}:
                path = msg["path"]
                if '/' in path:
                    if self.current_output_item is None:
                        raise ValueError("no current output item")
                    self.current_output_item.validate_path(path)
                else:
                    if self.current_output_item is not None:
                        raise ValueError("an output item is already present")

                    self.current_output_item = OutputItem(
                        path,
                        isdir=(cmd == 'output-dir')
                    )

                if cmd == 'output-file':
                    # prevent attackers from using negative integers/floats
                    size = abs(int(msg["size"]))
                else:
                    size = 0

                # also account for metadata size
                # (prevent DOSers from creating billions of empty files)
                self.current_output_item.size += (size + 512)
                if self.current_output_item.size > self.remaining_output_size:
                    raise ValueError("output size limit exceeded")

                pathobj = self.path.joinpath(path)
                if pathobj.exists():
                    raise ValueError("duplicate output path: " + path)

                if cmd == 'output-file':
                    raw_file, raw_remaining = pathobj.open('wb'), size
                else:
                    pathobj.mkdir()

            else:
                raise ValueError("unknown build control command: " + repr(cmd))
