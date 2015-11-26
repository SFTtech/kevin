# kevin-ci

A simple-stupid self-hosted continuous integration service in >=Python3.4.


### Dafuq?

Kevin is a self-hostable CI daemon to build pull requests inside your VMs.


It was mainly developed for [openage](http://openage.sft.mx/),
but you can use it for _any_ project!

Kevin can create doc files, bundle software, run tests, make screenshots,
run any container/VM.


### How?

* Your kevin instance is notified by a github webhook.
* It spawns a temporary VM for the job.
* The repo is cloned and the build/test steps in `.kevin` are executed.
* Progress can be viewed live via github, `curl`, websocket or website.
* Results are instantly reported to github.


### Why kevin?

* Totally simple-stupid
* Configurability and customization
* Fast!
* Self-hostable.


### [Supported containers](/falk/vm/)

* [qemu](http://qemu-project.org) (kvm)
* prepared:
 * [docker](https://www.docker.com/)
 * [xen](https://www.xenproject.org/)
 * [nspawn](http://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
 * [lxc](https://linuxcontainers.org/)
 * [rkt](https://coreos.com/rkt/docs/latest/)
 * [clearlinux](https://clearlinux.org/)


### Setup

The `kevin` component interacts with the outside world.
You need a server for it.

`kevin` contacts `falk` to start a VM. You need a server for `falk` again,
but it can be the same machine where `kevin` is running on.

`falk` launches virtual machines created by you.
You create the machine template (just set up a debian...) or use docker, etc.
Your job is then built inside that container by `chantal`.


#### Build host

##### Host system (Kevin)

 - Install
  - `python >=3.4`
  - `tornado`
  - `requests`
 - Create user `kevin`
  - Add `kevin` to group `kvm`
  - Create `kevin.conf` from [`kevin.conf.example`](dist/kevin.conf.example)
  - Create a password-less SSH key with `ssh-keygen -t rsa -b 4096`
 - Install the `kevin` Python module (ideally, as a systemd unit or whatever)

##### VM provider (Falk)

 - Install
  - `python >=3.4`
  - your container system of choice: qemu, ...

 - If it's a different machine than the host `kevin` above:
  - Create user `falk`
  - Setup password-less SSH access (`ssh-copy-id`) for above `kevin` user to `falk@vmserver.name`
  - Force ssh command to `command="python3 -m falk.shell useridentifier" ssh-rsa keyblabla...`
  - Configure `kevin` in the `[falk]` section: add `falk_name=falk@vmserver.name`

 - If it's running on the same machine as `kevin`:
  - Configure `kevin` in the `[falk]` section: add `falk_name=userid@/run/kevin/falk`

 - Create `falk.conf` from [`falk.conf.example`](dist/falk.conf.example)
 - TODO/optional: create firewall rules to prevent the VM from talking to internals of your network


##### Guest systems (Chantal)

 - Setup the OS, create user `chantal`
 - If your build process includes graphical stuff, setup some autologin stuff.
 - You need `python >=3.4`, `git`, `ssh`, `sudo`
 - It's convenient to have some VNC with e.g. qemu, `tigervnc` or `x11vnc`
 - In `visudo`, give `NOPASSWD: ALL` permissions to `chantal`
 - Setup password-less SSH access (`ssh-copy-id`) for above `kevin` user to `chantal@guest`


#### Project

 - Create [control file](dist/controlfile.example) `.kevin` in project root
 - Create GitHub webhook:
  - Settings > Webhooks and Services > Add Webhook
  - content type: JSON, URL: `http://your-host:webhook_port/hook-github`
