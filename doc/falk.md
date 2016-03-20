Falk machine provider
=====================

Falk is the daemon which executes the containers/virtual machines.


Managing VMs
------------

Once your VM is created (see [setup.md](setup.md)), and falk is running,
you can launch and SSH into it.

The `falk.manage` helper boots the machine in management mode
and opens an ssh shell in it.

``` bash
python -m falk.manage unix://mom@/run/kevin/falk my-vm-id $optional-command
```

It uses the exact same access kevin would use,
except the machine is persistent.

In there, update the machine, install packages, whatever.
All jobs will copy that image to run on.

If you want a temporary machine (like a job gets), call `falk.manage --volatile`.
