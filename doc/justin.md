Justin machine provider
=======================

Justin is the daemon which executes the containers/virtual machines.

Justin starts them when Kevin requests a machine and cleans them up afterwards.

The backends are implemented in [`justin/machine/`](/justin/machine), and there's a [configuration guide for each backend](machine/).


Managing VMs
------------

Once your VM is created (see [setup.md](setup.md)), and justin is running,
you can launch and SSH into it.

The `justin.manage` helper boots the machine in management mode
and opens an ssh shell in it.

``` bash
python -m justin.manage unix:///run/kevin/justin my-machine-name $optional-command
```

It uses the exact same access kevin would use,
except the machine is persistent.

In there, update the machine, install packages, whatever.
All jobs will copy that image to run on.

If you want a temporary machine (like a job gets), call `justin.manage --volatile`.
