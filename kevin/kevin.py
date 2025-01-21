"""
Main Queue and Falk management entity.
"""

from __future__ import annotations

import asyncio
import typing

from .build_manager import BuildManager
from .httpd import HTTPD
from .job_manager import JobManager
from .task_queue import TaskQueue

if typing.TYPE_CHECKING:
    from .config import Config


async def run(config: Config):
    """
    This is Kevin. He will build your job. Guaranteed to be bug-free(*).

    Jobs from various sources are put into the Queue,
    which is processed by running each jobs via Falk with Chantal.

    (*) Disclaimer: May not actually be bug-free.

    Runs Kevin foreveeeeeerrrrrrrrrr!
    """

    # job distribution
    job_manager = JobManager(asyncio.get_running_loop(), config)

    # build creation
    build_manager = BuildManager()

    # queue where build jobs will end up in
    queue = TaskQueue(asyncio.get_running_loop(), job_manager,
                      config.max_jobs_running, config.max_jobs_queued)

    # webserver: receives hooks and provides websocket api
    httpd = HTTPD(config.urlhandlers, queue, build_manager)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(queue.run())
            tg.create_task(job_manager.run())

    except asyncio.CancelledError:
        # stop http listening
        await httpd.stop()
        raise
