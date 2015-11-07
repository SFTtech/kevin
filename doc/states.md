Job states
==========

| Job state                    | In Filesystem                     | In Python interpreter |
|------------------------------|-----------------------------------|-----------------------|
| Freshly received             | job_id doesn't exist              | in jobs.ACTIVE        |
|                              |                                   | in httpd.job_queue    |
|------------------------------|-----------------------------------|-----------------------|
| Building                     | job_id/generated exists           | in jobs.ACTIVE        |
|                              |                                   | job.build running     |
|------------------------------|-----------------------------------|-----------------------|
| Completed-Cached             | job_id/generated exists           | in jobs.CACHED        |
| (failure or success)         | job_id/completed exists           |                       |
|                              | job_id/log exists                 |                       |
|------------------------------|-----------------------------------|-----------------------|
| Completed                    | job_id/generated exists           | nowhere               |
| (failure or success)         | job_id/completed exists           |                       |
|                              | job_id/log exists                 |                       |
|------------------------------|-----------------------------------|-----------------------|

Transitions
===========

| Event             | State from       | State to         | Actions                                                      |
|-------------------|------------------|------------------|--------------------------------------------------------------|
| Webhook           |                  | Freshly received | Add to jobs.ACTIVE, httpd.job_queue                          |
| Builder available | Freshly received | Building         | call job.build(), mkdir job_id/generated                     |
| Abort, Finish     | Building         | Completed-cached | Add to jobs.CACHED, touch job_id/completed, write job_id/log |
| Cache full        | Completed-Cached | Completed        | Remove from jobs.CACHED                                      |
| Retry             | Completed/Cached | Freshly received | rm -r job_id, add to jobs.ACTIVE, httpd.job_queue            |
|-------------------|------------------|------------------|--------------------------------------------------------------|
