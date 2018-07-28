"""
Job processing code
"""

import asyncio
import json
import logging
import shutil
import time as clock
import traceback

from .action import Action
from .chantal import Chantal
from .config import CFG
from .falkvm import VMError
from .process import (ProcTimeoutError, ProcessFailed, ProcessError)
from .update import (Update, JobState, JobUpdate, StepState,
                     StdOut, OutputItem, QueueActions, JobCreated,
                     GeneratedUpdate, JobEmergencyAbort, RegisterActions)
from .watchable import Watchable
from .watcher import Watcher


class JobTimeoutError(Exception):
    """
    Indicates a job execution timeout.

    was_global=True: termination time limit reached
    was_global=False: output silence limit reached
    """
    def __init__(self, timeout, was_global):
        super().__init__()
        self.timeout = timeout
        self.was_global = was_global


class JobAction(Action):
    """
    This action attaches a new job to a build.

    The inheritance alone leads to the availability of
    a [job] action in the project config file.
    """

    @classmethod
    def name(cls):
        return "job"

    def __init__(self, cfg, project):
        super().__init__(cfg, project)
        self.job_name = cfg["name"]
        self.descripton = cfg.get("description")
        self.vm_name = cfg["machine"]

    async def get_watcher(self, build, completed):
        if completed:
            return None

        new_job = Job(build, self.project, self.job_name, self.vm_name)
        await new_job.send_creation_notification()
        return new_job


class Job(Watcher, Watchable):
    """
    Holds all info for one job, which runs a commit SHA of a project
    in a falk machine.

    TODO: when "restarting" the job, the reconstruction from fs must
          not happen. for that, a "reset" must be implemented.
          The restart is not implemented either, though.
    """
    def __init__(self, build, project, name, vm_name):
        super().__init__()

        # the project and build this job is invoked by
        self.build = build
        self.project = project

        # name of this job within a build.
        self.name = name

        # name of the vm where the job shall run.
        self.vm_name = vm_name

        # No more tasks to perform for this job?
        # If the job is completed, stores the completion timestamp (float).
        # else, stores None.
        self.completed = None

        # the tasks required to run for this job.
        self.tasks = set()

        # List of job status update JSON objects.
        self.updates = list()

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
        self.remaining_output_size = self.project.cfg.job_max_output

        # receive files from chantal
        self.raw_file = None
        self.raw_remaining = 0

        # storage folder for this job.
        self.path = build.path.joinpath(self.name)

        # are all updates from the job in the update list already?
        self.all_loaded = False

        # check if the job was completed.
        # this means we can do a reconstruction.
        if not CFG.args.volatile:
            try:
                self.completed = self.path.joinpath("_completed").stat().st_mtime
            except FileNotFoundError:
                pass

    async def send_creation_notification(self):
        """
        Tell the our watchers that this job is was created
        """
        await self.send_update(JobCreated(self.name, self.vm_name))

    async def load_from_fs(self):
        """
        Ensure the job is reconstructed from filesystem.
        """

        if CFG.args.volatile:
            return False

        updates_file = self.path.joinpath("_updates")

        if not updates_file.is_file():
            return False

        if not self.all_loaded:
            with updates_file.open() as updates_file:
                for json_line in updates_file:
                    await self.send_update(Update.construct(json_line),
                                           reconstruct=True)

            logging.debug(f"reconstructed Job {id(self)} {self} from fs")

            # jup, we reconstructed.
            self.all_loaded = True

        return True

    def purge_fs(self):
        """
        Remove all the files from this build.
        """

        if CFG.args.volatile:
            return False

        # make sure that there are no remains
        # of previous aborted jobs.
        try:
            shutil.rmtree(str(self.path))
        except FileNotFoundError:
            pass

        # create the output directory structure
        if not self.path.is_dir():
            self.path.mkdir(parents=True)

    def __str__(self):
        return "%s.%s [\x1b[33m%s\x1b[m]" % (
            self.build.project.name,
            self.name,
            self.build.commit_hash)

    def on_send_update(self, update, reconstruct=False):
        """
        When an update is to be sent to all watchers
        """

        if update is StopIteration:
            return

        # store the update in the list of updates
        # that are replayed to each new subscriber
        replay = True

        # when reconstructing, the update just came from the file
        fs_save = not reconstruct

        if not reconstruct and self.completed:
            raise Exception("job wants to send update "
                            f"after being completed: {update}")

        if isinstance(update, JobUpdate):
            # run the job-update hook.
            # when it's a StepUpdate, this manages the pending_steps set.
            update.apply_to(self)

        if isinstance(update, GeneratedUpdate):
            replay = False
            fs_save = False

        if isinstance(update, JobCreated):
            # this is stored by the build.
            fs_save = False

        if replay:
            self.updates.append(update)

        if not fs_save or CFG.args.volatile:
            # don't write the update to the job storage
            return

        # append this update to the build updates file
        # TODO perf: don't open _updates on each update!
        with self.path.joinpath("_updates").open("a") as ufile:
            ufile.write(update.json() + "\n")

    async def on_update(self, update):
        """
        When this job receives updates from any of its watched
        watchables, the update is processed here.

        That means normally: the Build notifies this Job.
        """

        if isinstance(update, RegisterActions):
            await self.register_to_build()

        elif isinstance(update, QueueActions):
            if not self.all_loaded:

                if self.completed:
                    # we can reconstruct as the _completed file exists.
                    await self.load_from_fs()

                else:
                    self.purge_fs()
                    # run the job by adding it to the processing queue
                    await update.queue.add_job(self)
                    await self.set_state("waiting", "enqueued")

    def step_update(self, update):
        """ apply a step update to this job. """

        if not isinstance(update, StepState):
            raise Exception("tried to use non-StepState to step_update")

        if update.state == "pending":
            self.pending_steps.add(update.step_name)
        else:
            try:
                self.pending_steps.remove(update.step_name)
            except KeyError:
                pass

        if update.step_number is None:
            if update.step_name not in self.step_numbers:
                self.step_numbers[update.step_name] = len(self.step_numbers)
            update.step_number = self.step_numbers[update.step_name]

    async def set_state(self, state, text, time=None):
        """ set the job state information """
        await self.send_update(JobState(self.project.name, self.build.commit_hash,
                                        self.name, state, text, time))


    async def set_step_state(self, step_name, state, text, time=None):
        """ send a StepState update. """
        await self.send_update(StepState(self.project.name, self.build.commit_hash,
                                         self.name, step_name, state, text,
                                         time=time))

    async def on_watcher_registered(self, watcher):
        # send all previous job updates to the watcher
        for update in self.updates:
            await watcher.on_update(update)

        # and send stop if this job is finished
        if self.completed:
            await watcher.on_update(StopIteration)

    async def register_to_build(self):
        """
        Register this job to its build.
        """
        # tell the build that we're an assigned job.
        self.build.register_job(self)

        # we are already attached to receive updates from a build
        # now, we subscribe the build to us so it gets our updates.
        await self.register_watcher(self.build)

    async def run(self, job_manager):
        """ Attempts to build the job. """

        try:
            if self.completed:
                raise Exception("tried to run a completed job!")

            # falk contact
            await self.set_state("waiting", "requesting machine")

            # figure out which machine to run the job on
            machine = await asyncio.wait_for(
                job_manager.get_machine(self.vm_name),
                timeout=10
            )

            # machine was acquired, now boot it.
            await self.set_state("waiting", "booting machine")

            async with Chantal(machine) as chantal:

                # wait for the machine to be reachable
                await chantal.wait_for_connection(timeout=60, try_timeout=20)

                # install chantal (someday, might be preinstalled)
                await chantal.install(timeout=20)

                # create control message parser sink
                control_handler = self.control_handler()
                # advance to the first yield
                await control_handler.asend(None)

                error_messages = bytearray()
                try:
                    # run chantal process in the machine
                    async with chantal.run(self) as run:

                        # and fetch all the output
                        async for stream_id, data in run.output():
                            if stream_id == 1:
                                # control message stream chunk
                                await control_handler.asend(data)
                            else:
                                error_messages += data

                        # wait for chantal termination
                        await run.wait()

                except ProcTimeoutError as exc:
                    raise JobTimeoutError(exc.timeout, exc.was_global)

                except ProcessFailed as exc:
                    error_messages += b"process returned %d" % exc.returncode

                if error_messages:
                    error_messages = error_messages.decode(errors='replace')
                    logging.error("[job] \x1b[31;1mChantal failed\x1b[m\n%s", error_messages)
                    await self.error("Chantal failed; stdout: %s" %
                                     error_messages.replace("\n", ", "))

        except asyncio.CancelledError:
            logging.info("\x1b[31;1mJob aborted:\x1b[m %s", self)
            await self.error("Job cancelled")
            raise

        except (ProcessFailed, asyncio.TimeoutError) as exc:
            if isinstance(exc, ProcessFailed):
                error = ": %s" % exc.cmd[0]
                what = "failed"
            else:
                error = ""
                what = "timed out"

            logging.error("[job] \x1b[31;1mProcess %s:\x1b[m %s:\n%s",
                          what, self, exc)

            await self.error("Process failed%s" % error)

        except ProcTimeoutError as exc:
            if exc.was_global:
                silence = "Took"
            else:
                silence = "Silence time"

            logging.error("[job] \x1b[31;1mTimeout:\x1b[m %s", self)
            logging.error(" $ %s", " ".join(exc.cmd))
            logging.error("\x1b[31;1m%s longer than limit "
                          "of %.2fs.\x1b[m", silence, exc.timeout)
            traceback.print_exc()

            await self.error("Process %s took > %.02fs." % (exc.cmd[0],
                                                            exc.timeout))

        except ProcessError as exc:
            logging.error("[job] \x1b[31;1mCommunication failure:"
                          "\x1b[m %s", self)

            logging.exception("This was kinda unexpected...")

            await self.error("SSH Process communication error")

        except JobTimeoutError as exc:

            # did it take too long to finish?
            if exc.was_global:
                logging.error("[job] \x1b[31;1mTimeout:\x1b[m %s", self)
                logging.error("\x1b[31;1mTook longer than limit "
                              "of %.2fs.\x1b[m", exc.timeout)

                if self.current_step:
                    await self.set_step_state(self.current_step, "error",
                                              "Timeout!")

                await self.error("Job took > %.02fs." % (exc.timeout))

            # or too long to provide a message?
            else:
                logging.error("[job] \x1b[31;1mSilence timeout!\x1b[m "
                              "%s\n\x1b[31mQuiet for > %.2fs.\x1b[m",
                              self, exc.timeout)

                # a specific step is responsible:
                if self.current_step:
                    await self.set_step_state(self.current_step, "error",
                                              "Silence for > %.02fs." % (
                                                  exc.timeout))
                    await self.error("Silence Timeout!")
                else:
                    # bad step is unknown:
                    await self.error("Silence for > %.2fs!" % (exc.timeout))

        except VMError as exc:
            logging.error("\x1b[31;1mMachine action failed\x1b[m "
                          "%s.%s [\x1b[33m%s\x1b[m]",
                          self.build.project.name,
                          self.name,
                          self.build.commit_hash)
            traceback.print_exc()

            await self.error("VM Error: %s" % (exc))

        except Exception as exc:
            logging.error("\x1b[31;1mexception in Job.run()\x1b[m "
                          "%s.%s [\x1b[33m%s\x1b[m]",
                          self.build.project.name,
                          self.name,
                          self.build.commit_hash)
            traceback.print_exc()

            try:
                # make sure the job dies
                await self.error("Job.run(): %r" % (exc))
            except Exception as exc:
                # we end up here if the error update can not be sent,
                # because one of our watchers has a programming error.
                # some emergency handling is required.

                logging.error(
                    "\x1b[31;1mjob failure status update failed again! "
                    "Performing emergency abort, waaaaaah!\x1b[m")
                traceback.print_exc()

                try:
                    # make sure the job really really dies
                    await self.send_update(
                        JobEmergencyAbort(
                            self.project.name,
                            self.build.commit_hash,
                            self.name,
                            "Killed job after double fault of job."
                        )
                    )
                except Exception as exc:
                    logging.error(
                        "\x1b[31;1mOk, I give up. The job won't die. "
                        "I'm so Sorry.\x1b[m")
                    traceback.print_exc()

        finally:
            # error the leftover steps
            for step in list(self.pending_steps):
                await self.set_step_state(step, 'error',
                                          'step result was not reported')

            # the job is completed!
            self.completed = clock.time()
            self.all_loaded = True

            if not CFG.args.volatile:
                # the job is now officially completed
                self.path.joinpath("_completed").touch()

            await self.send_update(StopIteration)

    async def error(self, text):
        """
        Produces an 'error' JobState and an 'error' StepState for all
        steps that are currently pending.
        """
        while self.pending_steps:
            step = self.pending_steps.pop()
            await self.set_step_state(step, "error", "build has errored")

        await self.set_state("error", text)

    async def control_handler(self):
        """
        Coroutine that receives control data chunks via yield, and
        interprets them.
        Note: The data chunks are entirely untrusted.
        """
        data = bytearray()

        while True:

            # transfer files through raw binary mode
            if self.raw_file is not None:
                if not data:
                    # wait for more data
                    data += yield

                if len(data) >= self.raw_remaining:
                    # the file is nearly complete,
                    # the remaining data is the next control message

                    self.raw_file.write(data[:self.raw_remaining])
                    self.raw_file.close()
                    self.raw_file = None

                    del data[:self.raw_remaining]

                else:
                    # all the data currently buffered shall go into the file
                    self.raw_file.write(data)
                    self.raw_remaining -= len(data)
                    del data[:]

                continue

            # control stream is in regular JSON+'\n' mode
            newline = data.find(b"\n")
            if newline < 0:
                if len(data) > (8 * 1024 * 1024):
                    # chantal is trying to crash us with a >= 8MiB msg
                    raise ValueError("Control message too long")

                # wait for more data
                data += yield
                continue

            # parse the line as control message,
            # after the newline may be raw data or the next control message
            msg = data[:newline + 1].decode().strip()
            if msg:
                await self.control_message(msg)

            del data[:newline + 1]

    async def control_message(self, msg):
        """
        control message parser, chantal sends state through this channel.
        """

        msg = json.loads(msg)

        cmd = msg["cmd"]

        if cmd == 'job-state':
            await self.set_state(msg["state"], msg["text"])

        elif cmd == 'step-state':
            self.current_step = msg["step"]
            await self.set_step_state(msg["step"], msg["state"], msg["text"])

        elif cmd == 'stdout':
            await self.send_update(StdOut(
                self.name,
                msg["text"]
            ))

        # finalize file transfer
        elif cmd == 'output-item':
            name = msg["name"]

            if self.current_output_item is None:
                raise ValueError("no data received for " + name)
            if self.current_output_item.name != name:
                raise ValueError(
                    "wrong output item name: " + name + ", "
                    "expected: " + self.current_output_item.name
                )

            await self.send_update(self.current_output_item)
            self.current_output_item = None

        # file or folder transfer is announced
        elif cmd in {'output-dir', 'output-file'}:
            path = msg["path"]

            if not path:
                raise ValueError("invalid path: is empty")

            if path[0] in {".", "_"}:
                raise ValueError("invalid start of output filename: . or _")

            if '/' in path:
                # a file with / is emitted, it must be some subdirectory/file
                # -> ensure this happens as part of an output item.
                if self.current_output_item is None:
                    raise ValueError("no current output item")
                self.current_output_item.validate_path(path)

            else:
                if self.current_output_item is not None:
                    raise ValueError("an output item is already present")

                self.current_output_item = OutputItem(
                    self.name,
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
            if pathobj.exists() and not CFG.args.volatile:
                raise ValueError("duplicate output path: " + path)

            self.raw_remaining = size

            if CFG.args.volatile:
                logging.warning("'%s' ignored because of "
                                "volatile mode active: %s", cmd, pathobj)
                self.raw_file = open("/dev/null", 'wb')

            elif cmd == 'output-file':
                self.raw_file = pathobj.open('wb')

            elif cmd == 'output-dir':
                pathobj.mkdir(parents=True, exist_ok=True)

            else:
                raise ValueError("unknown command: {}".format(cmd))

        else:
            raise ValueError("unknown build control command: %r" % (cmd))
