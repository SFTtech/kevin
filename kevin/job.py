"""
Job processing code
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import shlex
import time as clock
import typing
from collections import defaultdict

from .action import Action
from .chantal import Chantal
from .config import CFG
from .justin_machine import MachineError
from .process import (ProcTimeoutError, ProcessFailed, ProcessError)
from .update import (Update, JobState, JobUpdate, JobStarted, JobFinished,
                     StdOut, OutputItem, QueueActions, StepState,
                     GeneratedUpdate, JobEmergencyAbort, RegisterActions, BuildFinished)
from .watchable import Watchable
from .watcher import Watcher

if typing.TYPE_CHECKING:
    from .build import Build
    from .job_manager import JobManager
    from .project import Project
    from typing import TextIO


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

    This is what causes a new job to be created.
    In order for it to be reconstructed from storage, Build re-creates it
    due to the BuildJobCreated updates it persisted.
    """

    def __init__(self, cfg, project: Project):
        super().__init__(cfg, project)
        self.job_name = cfg["name"]
        self.descripton = cfg.get("description")
        self.machine_name = cfg["machine"]

    async def get_watcher(self, build: Build, completed: bool) -> Watcher | None:
        if completed:
            # when the build is completed, jobs don't need to re-run
            return None

        return await build.create_job(self.job_name, self.machine_name)


class Job(Watcher, Watchable):
    """
    Holds all info for one job, which runs a commit SHA of a project
    in a justin machine.

    TODO: when "restarting" the job, the reconstruction from fs must
          not happen. for that, a "reset" must be implemented.
          The restart is not implemented either, though.
    """
    def __init__(self, build: Build, project: Project, name: str, machine_name: str):
        super().__init__()

        # the project and build this job is invoked by
        self.build = build
        self.project = project

        # name of this job within a build.
        self.name = name

        # name of the machine where the job shall run.
        self.machine_name = machine_name

        # No more tasks to perform for this job?
        # If the job is completed, stores the completion timestamp (float).
        # else, stores None.
        self.completed = None

        # did this job emit a JobState that is successful
        self._success = False

        # List of job status update JSON objects.
        self._updates: list[JobUpdate] = list()

        # Internal cache, used when erroring all remaining pending states.
        self.pending_steps: set[str] = set()

        # {step name: step number}, used for step name prefixes
        self.step_numbers: dict[str, int] = dict()

        # all commited output items
        self.output_items: set[OutputItem] = set()

        # current uncommited output item
        self._current_output_item = None

        # current step name of the job
        self._current_step: str | None = None

        # remaining size limit
        self.remaining_output_size = self.project.cfg.job_max_output

        # receive files from chantal
        self._output_raw_file = None
        self._output_raw_remaining = 0

        # storage folder for this job.
        self.path = build.path.joinpath(self.name)

        # the job updates storage file
        self._updates_fd: TextIO | None = None

        # if all our updates are combined
        self._updates_merged = False

        # are all updates from the job in the update list already?
        self._all_loaded = False

        # check if the job was completed.
        # this means we can do a reconstruction.
        if not CFG.volatile:
            try:
                self.completed = self.path.joinpath("_completed").stat().st_mtime
            except FileNotFoundError:
                pass

    async def _merge_updates(self) -> None:
        """
        merge all updates to only remember their effective end result.
        output is combined and only the latest states are retained.
        """

        if self._updates_merged:
            return

        if CFG.volatile:
            return

        # last JobState
        job_state: JobState | None = None
        # all step_states. step_name -> state
        step_states: dict[str, StepState] = dict()
        # step_name -> output strings
        output: dict[str | None, list[str]] = defaultdict(list)
        other_updates: list[JobUpdate] = list()

        current_step: str | None = None
        step_order: list[str] = list()
        seen_steps: set[str] = set()

        for update in self._updates:
            match update:
                case GeneratedUpdate():
                    # generated updates are not persistent.
                    continue

                case JobState():
                    job_state = update
                case StepState():
                    step_states[update.step_name] = update
                    if update.text != "waiting":
                        current_step = update.step_name
                        if current_step is None:
                            raise Exception("no step name for step state update")
                        if current_step not in seen_steps:
                            step_order.append(current_step)
                            seen_steps.add(current_step)
                case StdOut():
                    # we need to chunk/interleave the output with StepState
                    # otherwise anchors are not placed correctly!

                    # we track the current_step above for backward compatibility
                    # new versions of StdOut know their step_name on their own!
                    output[update.step_name or current_step].append(update.data)
                case JobUpdate():
                    other_updates.append(update)
                case _:
                    raise ValueError("non JobUpdate event found to be stored in a Job")

        # merge all output
        if job_state is None:
            raise RuntimeError("no JobState found in job updates")

        job_state.set_updates_merged()

        updates: list[JobUpdate] = [job_state]

        if pre_output := output.get(None):
            updates.append(StdOut(job_name=self.name,
                                  data="".join(pre_output)))
        for step_name in step_order:
            step_state = step_states[step_name]
            updates.append(step_state)
            if step_output := output.get(step_name):
                # TODO: maybe do chunk step_output if it's really huge...
                updates.append(StdOut(job_name=self.name,
                                      step_name=step_name,
                                      data="".join(step_output)))
        updates.extend(other_updates)

        if self._updates_fd is not None:
            self._updates_fd.close()
            self._updates_fd = None

        merged_update_path = self.path.joinpath("_updates_merged")
        with merged_update_path.open("w") as fd:
            for update in updates:
                fd.write(update.json())
                fd.write("\n")

        # replace the old updates file
        merged_update_path.rename(self.path.joinpath("_updates"))
        self._updates = updates
        self._updates_merged = True

        # build caches the update messages - so it needs to be notified.
        await self.build.merge_job_updates(self.name, self._updates)

    async def load(self) -> bool:
        """
        maybe load a job from permanent storage.

        returns: is it was reconstructed.
        """

        if CFG.volatile:
            return False

        if not self._all_loaded:
            return await self._load_from_fs()

        return False

    async def _load_from_fs(self):
        """
        Ensure the job is reconstructed from filesystem.
        """

        if self._all_loaded:
            raise Exception("tried to load job that is loaded already")

        # lines of json, for incremental updates
        updates_file = self.path.joinpath("_updates")

        if not updates_file.is_file():
            return False

        is_merged = False
        with updates_file.open() as updates_file:
            for json_line in updates_file:
                update = Update.construct(json_line)

                # migrate to merged job updates
                if isinstance(update, JobState):
                    if update.updates_merged:
                        is_merged = True

                await self.send_update(update, reconstruct=True)

        if not is_merged:
            self._merge_updates()

        logging.debug("reconstructed %s from fs", self)

        # jup, we reconstructed.
        self._all_loaded = True

        return True

    def _purge_fs(self):
        """
        Remove all the files from this build.
        """

        if CFG.volatile:
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
        return f"<Job {self.build.project.name}.{self.name} [\x1b[33m{self.build.commit_hash}\x1b[m]>"

    def on_send_update(self, update: Update, **kwargs):
        """
        When an update is to be sent to all watchers
        """

        reconstruct = kwargs.get("reconstruct", False)
        # when reconstructing, the update just came from the file
        fs_save = not reconstruct

        if not reconstruct and self.completed:
            raise Exception("job wants to send update "
                            f"after being completed: {update}")

        if not isinstance(update, JobUpdate):
            # we only send job updates
            raise Exception(f"sent non job-update from job: {update}")

        # run the job-update hook.
        # when it's a StepUpdate, this manages the pending_steps set.
        update.apply_to(self)

        # store the update in the list of updates
        # that are replayed to each new subscriber
        self._updates.append(update)

        if isinstance(update, JobState):
            # the updates-merged flag is only ever set after a job ran through
            # so we can assume it for all other updates.
            # the merging retains only one JobState, so we can track merge-status through it.
            self._updates_merged = update.updates_merged

            # whooh the job did its job!
            if update.is_succeeded():
                self._success = True

        if isinstance(update, GeneratedUpdate):
            fs_save = False

        if not fs_save or CFG.volatile:
            # don't write the update to the job storage
            return

        # append this update to the build updates file
        if self._updates_fd is None:
            self._updates_fd = self.path.joinpath("_updates").open("a")

        self._updates_fd.write(update.json())
        self._updates_fd.write("\n")

    async def on_update(self, update: Update):
        """
        When this job receives updates from any of its watched
        watchables, the update is processed here.

        That means normally: the Build notifies this Job to queue/run itself.
        """

        match update:
            case RegisterActions():
                await self.register_to_build()

            case QueueActions():
                if not self._all_loaded:

                    if self.completed:
                        # we can reconstruct as the _completed file exists.
                        await self.load()

                    else:
                        self._purge_fs()
                        # run the job by adding it to the processing queue
                        await update.queue.add_job(self)
                        await self.set_state("waiting", "enqueued")

            case BuildFinished():
                self.build.deregister_watcher(self)

    def step_update(self, update: StepState):
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
        state = JobState(self.project.name, self.build.commit_hash,
                         self.name, state, text, time)
        await self.send_update(state)

    async def set_step_state(self, step_name, state, text, time=None):
        """ send a StepState update. """
        await self.send_update(StepState(self.project.name, self.build.commit_hash,
                                         self.name, step_name, state, text,
                                         time=time))

    async def on_watcher_registered(self, watcher: Watcher):
        # send all previous job updates to the watcher
        for update in self._updates:
            await watcher.on_update(update)

    async def register_to_build(self) -> None:
        """
        Register this job to its build.
        """
        # tell the build that we're an assigned job.
        self.build.register_job(self)

        # we are already attached to receive updates from a build
        # now, we subscribe the build to us so it gets our updates.
        await self.register_watcher(self.build)

    async def run(self, job_manager: JobManager):
        """ Attempts to build the job. """

        try:
            if self._all_loaded:
                raise Exception("tried to run a job that was loaded from storage.")
            self._all_loaded = True

            if self.completed:
                raise Exception("tried to run a completed job!")

            await self.send_update(JobStarted(self.name))

            # justin contact
            await self.set_state("waiting", "requesting machine")

            # figure out which machine to run the job on
            machine = await asyncio.wait_for(
                job_manager.get_machine(self.machine_name),
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
                        logging.debug("chantal exited with %s", run.returncode())

                except ProcTimeoutError as exc:
                    raise JobTimeoutError(exc.timeout, exc.was_global)

                except ProcessFailed as exc:
                    error_messages += b"process returned %d" % exc.returncode

                if error_messages:
                    error_stdout = error_messages.decode(errors='replace')
                    logging.error("[job] \x1b[31;1mChantal failed\x1b[m\n%s", error_messages)
                    await self.error("Chantal failed; stdout: %s" %
                                     error_stdout.replace("\n", ", "))

        except asyncio.CancelledError:
            if not self._success:
                logging.info("\x1b[31;1mJob cancelled:\x1b[m %s", self)
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
            logging.error(" $ %s", shlex.join(exc.cmd))
            logging.exception("\x1b[31;1m%s longer than limit "
                              "of %.2fs.\x1b[m", silence, exc.timeout)

            await self.error("Process %s took > %.02fs." % (exc.cmd[0],
                                                            exc.timeout))

        except ProcessError:
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

                if self._current_step:
                    await self.set_step_state(self._current_step, "error",
                                              "Timeout!")

                await self.error("Job took > %.02fs." % (exc.timeout))

            # or too long to provide a message?
            else:
                logging.error("[job] \x1b[31;1mSilence timeout!\x1b[m "
                              "%s\n\x1b[31mQuiet for > %.2fs.\x1b[m",
                              self, exc.timeout)

                # a specific step is responsible:
                if self._current_step:
                    await self.set_step_state(self._current_step, "error",
                                              "Silence for > %.02fs." % (
                                                  exc.timeout))
                    await self.error("Silence Timeout!")
                else:
                    # bad step is unknown:
                    await self.error("Silence for > %.2fs!" % (exc.timeout))

        except MachineError as exc:
            logging.exception("\x1b[31;1mMachine action failed\x1b[m "
                              "%s.%s [\x1b[33m%s\x1b[m]",
                              self.build.project.name,
                              self.name,
                              self.build.commit_hash)

            await self.error("VM Error: %s" % (exc))

        except Exception as exc:
            logging.exception("\x1b[31;1mexception in Job.run()\x1b[m "
                              "%s.%s [\x1b[33m%s\x1b[m]",
                              self.build.project.name,
                              self.name,
                              self.build.commit_hash)

            try:
                # make sure the job dies
                await self.error("Job.run(): %r" % (exc))
            except Exception as exc:
                # we end up here if the error update can not be sent,
                # because one of our watchers has a programming error.
                # some emergency handling is required.

                logging.exception(
                    "\x1b[31;1mjob failure status update failed again! "
                    "Performing emergency abort, waaaaaah!\x1b[m")

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
                    logging.exception(
                        "\x1b[31;1mOk, I give up. The job won't die. "
                        "I'm so Sorry.\x1b[m")

        finally:
            # error the leftover steps
            for step in list(self.pending_steps):
                await self.set_step_state(step, 'error',
                                          'step result was not reported')

            if not CFG.volatile:
                # the job is now officially completed
                self.path.joinpath("_completed").touch()

            if self._updates_fd:
                self._updates_fd.close()
                self._updates_fd = None

            await self.send_update(JobFinished(self.name))

            # the job is completed!
            self.completed = clock.time()

            # perform state compression
            await self._merge_updates()

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
            if self._output_raw_file is not None:
                if not data:
                    # wait for more data
                    data += yield

                if len(data) >= self._output_raw_remaining:
                    # the file is nearly complete,
                    # the remaining data is the next control message

                    self._output_raw_file.write(data[:self._output_raw_remaining])
                    self._output_raw_file.close()
                    self._output_raw_file = None

                    del data[:self._output_raw_remaining]

                else:
                    # all the data currently buffered shall go into the file
                    self._output_raw_file.write(data)
                    self._output_raw_remaining -= len(data)
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
                await self._control_message(msg)

            del data[:newline + 1]

    async def _control_message(self, msg):
        """
        control message parser, chantal sends state through this channel.
        """

        msg = json.loads(msg)

        cmd = msg["cmd"]

        if cmd == 'job-state':
            await self.set_state(msg["state"], msg["text"])

        elif cmd == 'step-state':
            if msg["state"] != "waiting":
                self._current_step = msg["step"]
            await self.set_step_state(msg["step"], msg["state"], msg["text"])

        elif cmd == 'stdout':
            await self.send_update(StdOut(
                job_name=self.name,
                data=msg["text"],
                step_name=self._current_step,
            ))

        # finalize file transfer
        elif cmd == 'output-item':
            name = msg["name"]

            if self._current_output_item is None:
                raise ValueError("no data received for " + name)
            if self._current_output_item.name != name:
                raise ValueError(
                    "wrong output item name: " + name + ", "
                    "expected: " + self._current_output_item.name
                )

            await self.send_update(self._current_output_item)
            self._current_output_item = None

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
                if self._current_output_item is None:
                    raise ValueError("no current output item")
                self._current_output_item.validate_path(path)

            else:
                if self._current_output_item is not None:
                    raise ValueError("an output item is already present")

                self._current_output_item = OutputItem(
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
            self._current_output_item.size += (size + 512)
            if self._current_output_item.size > self.remaining_output_size:
                raise ValueError("output size limit exceeded")

            pathobj = self.path.joinpath(path)
            if pathobj.exists() and not CFG.volatile:
                raise ValueError("duplicate output path: " + path)

            self._output_raw_remaining = size

            if CFG.volatile:
                logging.warning("'%s' ignored because of "
                                "volatile mode active: %s", cmd, pathobj)
                self._output_raw_file = open("/dev/null", 'wb')

            elif cmd == 'output-file':
                self._output_raw_file = pathobj.open('wb')

            elif cmd == 'output-dir':
                pathobj.mkdir(parents=True, exist_ok=True)

            else:
                raise ValueError("unknown command: {}".format(cmd))

        else:
            raise ValueError("unknown build control command: %r" % (cmd))
