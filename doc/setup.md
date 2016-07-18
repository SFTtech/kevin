Kevin setup guide
=================

To set up kevin, you need 4 things:

* `kevin`: main daemon
* `falk`: container provider daemon (same repo)
* Set up your project to be tested
* And one or more containers/virtual machines


tl;dr
-----

* Install `kevin` on a server
* Create/edit `kevin.conf`, `falk.conf` and add a config for all your projects to be built
* Set up a VM or container and add it to `falk.conf`
* Allow `kevin` access to a github user
* Add `kevin-ci` as github webhook
* Run `kevin` and `falk`
* Add `.kevin` control file to your project
* Pull requests are built inside a container copy


Data flow
---------

* `kevin` interacts with the outside world (your git hoster).
  It gets notified by pull requests. You need a server for it.

* `kevin` contacts `falk` to start a VM. You need a server for `falk` again,
  but it can be the same machine where `kevin` is running on.
  There can be multiple `falk`s.

* `falk` launches a container/virtual machine provided by **you**. You
  create the machine template (just set up a debian...) or use docker, etc.
  Your pull request is then built inside that container by `chantal`.

* After the job was run, the machine is reverted back to the template.

You are responsible for managing and updating the container :smile:.
Which is one of the main reasons we created `kevin`.


Security design
---------------

`kevin` is designed in a way that arbitrary code can be executed in the
container and will not harm your host system, apart from the usual virtual
machine/container exit exploits that we can't prevent.

Our code is of course 100% bugfree, we even have created a program
to verify that kevin can never hang up. Hrr hrr...


Project config
--------------

Kevin supports hosting multiple projects.
Each project can spawn as many "jobs" as you like to have.

* **Build**: Equal to a commit hash. Spawns jobs which must succeed.
* **Job**: A container that runs *chantal*.
* **Step**: One step in a job, as defined in the `.kevin` file.
* **Command**: Each step runs commands.


#### Component setup

At the moment, only a `qemu` VM is supported.
Look inside [`falk/vm/`](/falk/vm) to add more containers.


##### Host system (Kevin)

 - Install
  - `python >=3.4`
  - `tornado`
  - `requests`
 - Create user `kevin` (you can, of course, change that)
  - Add `kevin` to group `kvm`
  - Create `/etc/kevin/kevin.conf` from [`kevin.conf.example`](/etc/kevin.conf.example)
  - Create a password-less SSH key with `ssh-keygen -t rsa -b 4096` for the user `kevin`
 - Install the `kevin` Python module (ideally, as a [systemd unit](/etc/kevin.service) or whatever)


##### VM provider (Falk)

 - Install
  - `python >=3.4`
  - your container system of choice: `qemu`, ...

 - Create `/etc/kevin/falk.conf` from [`falk.conf.example`](/etc/falk.conf.example)

 - Register this `falk` by adding it to the `kevin.conf` `[falk]` section.
  - If this `falk` is on the same machine as `kevin`:
    - add `falk_name=userid@/run/kevin/falk`
      and `kevin` will use this Unix socket to contact `falk`

  - If this `falk` is a **different physical machine** than the host for `kevin`:
    - Create user `falk` on the `falk` host
    - In `kevin.conf`, section `[falk]`, add `falk_name=falk@vmserver.name`,
      `kevin` will then contact falk via SSH
    - In `~falk/.ssh/authorized_keys`, force the ssh command to
      `command="python3 -m falk.shell useridentifier" ssh-rsa kevinkeyblabla...`
      This sets up password-less SSH access (`ssh-copy-id`..)
      for `kevin` to `falk@vmserver.name` and forces the falk shell.

 - optional: create firewall rules to prevent the VMs launched by `falk`
   from talking to internals of your network


##### Guest systems (Chantal)

 - [Setup the OS](https://wiki.archlinux.org/index.php/QEMU#Creating_new_virtualized_system)
 - Install
  - `python >=3.4`
  - `git`
  - `ssh` daemon
  - `sudo`
 - In the VM, create user `chantal`
 - In `visudo`, give `NOPASSWD: ALL` permissions to `chantal`
  - That way, `chantal` easily gets root permissions in the machine
 - Enable and run the `sshd` service
 - Setup password-less SSH access (`ssh-copy-id`) for above `kevin` user to `chantal@guest`
  - add `kevin`'s `id_rsa.pub` into `~chantal/.ssh/authorized_keys`
 - Store the contents of the container's `/etc/ssh/ssh_host_ed25519_key.pub`
   to the `falk.conf` so the key for this VM can be verified
 - If you do graphical things, maybe set up VNC with e.g. qemu, `tigervnc` or `x11vnc`
 - **Set up the container** in the way you'd like to test your project


##### Project

 - On the `kevin` machine,
   create a folder where project configurations reside in
  - `/etc/kevin/projects/` may be a good location
  - In there, create `lolmyproject.conf` from the
    [`etc/project.conf.example`](/etc/project.conf.example) file

 - For the project you want to test,
   create [control file](/etc/controlfile.example) `.kevin`
   in the project repo root (e.g. `~/dev/openage/.kevin`)

 - [Create GitHub webhook](https://developer.github.com/webhooks/creating/):
  - Settings > Webhooks and Services > Add Webhook
  - content type: JSON, URL: `http://your-kevin-host:webhook_port/hook-github`
  - Create a secret with e.g. `pwgen 1 30` and save it in the github webhook
    and your `projects/yourproject.conf` section `[github_webhook]`
  - Select events: **Pull Request** and **Push**


#### Running

* Persistent storage for `kevin` is done in the `[web]` `folder`
* Run `kevin` with `python3 -m kevin`
* Run `falk` with `python3 -m falk`
* [Manage a container](falk.md#managing-vms) with `python3 -m falk.manage`

* Test without `github`: Try using the [kevin simulator](simulator.md)
* Test with `github`: Just make a pull request!


### Contact

If you encounter any problem, please join our IRC channel and ask!

```
irc.freenode.net #sfttech
```

If things crash, bug, or whatever, create an issue!
