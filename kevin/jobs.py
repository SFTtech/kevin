"""
Job cache management
"""

from threading import Lock

from .job import Job

# map of job id -> new/building jobs
JOBS = dict()

# map of job id -> completed job
COMPLETED_JOB_CACHE = dict()

# guards JOBS and COMPLETED_JOB_CACHE
JOB_LOCK = Lock()


def get(job_id, create_new=True):
    """
    Searches for an existing job with the given id,
    and returns a tuple of (job, newly_created).
    If create_new is False, (None, False) is returned.
    """
    with JOB_LOCK:
        job = JOBS.get(job_id) or COMPLETED_JOB_CACHE.get(job_id)
        if job is None:
            job = Job(job_id)
            if job.completed:
                COMPLETED_JOB_CACHE[job_id] = job
            elif create_new:
                JOBS[job_id] = job
                return job, True
            else:
                job = None

        return job, False


def get_existing(job_id):
    """
    Like get(), but raises ValueError instead of creating a new job.
    """
    job, _ = get(job_id, False)
    if job is None:
        raise ValueError("no such job: " + job_id)
    return job


def put_in_cache(job):
    """
    Moves job from JOBS to COMPLETED_JOB_CACHE.
    """
    with JOB_LOCK:
        try:
            del JOBS[job.job_id]
        except KeyError:
            pass

        COMPLETED_JOB_CACHE[job.job_id] = job
