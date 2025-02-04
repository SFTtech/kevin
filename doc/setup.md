Kevin setup guide
=================

Components needed for a smooth experience\*:

* `Kevin`: Main daemon
* `Justin`: Container provider daemon
* `Chantal`: In-container agent to run the job
* `Mandy`: Webinterface

\* Disclaimer: The experience may not actually be smooth and likely more components are needed.


tl;dr
-----

* Install `kevin` on a server
* Create/edit `kevin.conf`, `justin.conf` and add a config for all your projects to be built
* Set up a VM or container with active SSH and add it to `justin.conf`
* Add `kevin-ci` as github webhook
* Run `kevin` and `justin` (e.g. as `systemd` service)
* Add the `kevinfile` control file to your project repo
* Pull requests are built inside a temporary container copy
* Set up a webserver to serve the `mandy` webinterface and your build output folder


Data flow
---------

* `kevin` interacts with the outside world (your git hoster).
  It gets notified by pull requests. You need a server for it.

* `kevin` contacts `justin` to start a VM. You need a server for `justin` again,
  but it can be the same machine where `kevin` is running on.
  There can be multiple `justin`s.

* `justin` launches a container/virtual machine provided by **you**. You
  create the machine template (just set up a debian...) or use docker, etc.
  Your pull request is then built inside that container by `chantal`.

* After the job was run, the machine is reverted back to the template.

* `mandy` presents the build progress in your browser in real-time.

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
* **Job**: Performed by *chantal* inside a container.
* **Step**: One step in a job, as defined in the in-repo `kevinfile` control file.
* **Command**: Each step runs one or more commands.


#### Component setup

We support different container backends.
Have a look inside [`justin/machine/`](/justin/machine) to see which ones and to add more container types.


##### Host system (Kevin)

Kevin gets a notification that it should build something, and kevin then notifies justin to provide a container.

- Install
  - `python >=3.11`
  - `aiohttp >=2.0`
- Create user `kevin` (you can, of course, change that)
  - Create `/etc/kevin/kevin.conf` from [`kevin.conf.example`](/etc/kevin.conf.example)
  - Create a password-less SSH key with `ssh-keygen -t rsa -b 4096` for the user `kevin`
- Install the `kevin` Python module (ideally, as a [systemd unit](/etc/kevin.service) or whatever)


##### Container provider (Justin)

Justin starts and cleans up containers when Kevin requests them.

- Install
  - `python >=3.6`
  - your container implementation of choice: `qemu`, `libpod`, `docker`, ...

- Create `/etc/kevin/justin.conf` from [`justin.conf.example`](/etc/justin.conf.example)

- Register this `justin` by adding it to the `kevin.conf` `[justin]` section.
  - If this `justin` is on the same machine as `kevin`:
    - add `justin_name=userid@/run/kevin/justin`
      and `kevin` will use this Unix socket to contact `justin`

  - If this `justin` is a **different physical machine** than the host for `kevin`:
    - Create user `justin` on the `justin` host
    - In `kevin.conf`, section `[justin]`, add `justin_name=justin@vmserver.name`,
      `kevin` will then contact justin via SSH
    - In `~justin/.ssh/authorized_keys`, force the ssh command to
      `command="python3 -m justin.shell useridentifier" ssh-rsa kevinkeyblabla...`
      This sets up password-less SSH access (`ssh-copy-id`..)
      for `kevin` to `justin@vmserver.name` and forces the justin shell.

- optional: create firewall rules to prevent the VMs launched by `justin`
   from talking to internals of your network


##### Guest systems (Chantal)

Setting up the guest system depends on the container technology you use.

* [Qemu](machine/vm.md)
* [Podman](machine/podman.md)
* [LXD](machine/lxd.md)
* Your [favorite-to-be-implemented](/justin/machine) backend


##### Project

The project config determines active Kevin plugins and their configuration.

- On the `kevin` machine,
   create a folder where project configurations reside in
  - `/etc/kevin/projects/` may be a good location
  - In there, create `lolmyproject.conf` from the
    [`etc/project.conf.example`](/etc/project.conf.example) file
  - For each project, create a file with a suitable name in `/etc/kevin/projects/`

- For the project you want to test,
  create [kevin control file](/etc/kevinfile.example) `kevinfile`
  in the project repo root (e.g. `~/dev/openage/kevinfile`)
  - You can change the name to anything you want, even [`kartoffelsalat`](https://www.youtube.com/watch?v=idKxckZiCsU)
  - If you change it, set the name in the `lolmyproject.conf`

- [Create GitHub webhook](https://developer.github.com/webhooks/creating/):
  - Settings > Webhooks and Services > Add Webhook
  - content type: JSON, URL: `http://your-kevin-host:webhook_port/hook-github`
  - Create a secret with e.g. `pwgen 1 30` and save it in the github webhook
    and your `projects/yourproject.conf` section `[github_webhook]`
  - Select events: **Pull Request** and **Push**


##### Webinterface (Mandy)

- Just serve the `mandy` folder on any machine in the world.
- Use `nginx` or `lighttpd` or `apache`, it does not matter.
- Enter the location where `mandy` can be reached in `kevin.conf`
- To allow output file downloads, you have to serve your static folder with
  another (or the same) webserver.


#### Testing

* You can **directly run Chantal** without all the other fuzz and see how it will build stuff.
* Kevin would do the same thing in the container/VM.
* To test, invoke `python3 -m chantal --help` and be enlightnened.
  * You can run Chantal inside your project without cloning it:
  * `python3 -m chantal --folder /your/project/repo $jobname`
  * `$jobname` is used for evaluating conditions in the `kevinfile`.
    Later, the `jobname` passed to Chantal will be taken from the job name
    you set up for a project in the its config file `lolmyproject.conf`.

  * The same test can be done within your VM/container:
  * `python3 -m chantal --folder /tmp/clonedest --clone $clone_url --checkout $branchname_or_hashname $jobname`



#### Running

* Persistent storage for `kevin` is done in `[projects]`/`output_folder`.
  * All build data will be stored there.
  * There's no cleanup mechanism yet (you may implemente it :)
* [systemd](https://www.freedesktop.org/wiki/Software/systemd/) setup
  * copy and adjust `etc/kevin.service` to `/etc/systemd/system/kevin.service`
  * copy and adjust `etc/justin.service` to `/etc/systemd/system/justin.service`
  * copy and adjust `etc/tmpfiles.d/kevin.conf` to `/etc/tmpfiles.d/kevin.conf`
  * enable the service with `systemctl enable $name.service`
  * start them with `systemctl start $name.service`
* Non-daemon launch
  * Run `kevin` with `python3 -m kevin`
  * Run `justin` with `python3 -m justin`
* After setup, [manage a container](justin.md#managing-vms) with `python3 -m justin.manage`
  * For example: `python3 -m justin.manage unix:///run/kevin/justin your_vm_id`

* We recommend to first test with a dummy repository that just contains a simple `kevinfile`, instead of the "real" project.
* Test without `github`: Try using the [Kevin Simulator](simulator.md)
* Test with `github`: Just make a pull request!


### Contact

If you encounter any problem, please [contact us](/README.md#contact) and ask!

If things crash, bug, or whatever, [create an issue](https://github.com/SFTtech/kevin-ci/issues)!

If you think this guide or anything else in this project is crap and sucks,
[just do better](https://github.com/SFTtech/kevin-ci/pulls)!
