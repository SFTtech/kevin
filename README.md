# kevin-ci

A simple-stupid self-hosted continuous integration service in Python.


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
* Progress can be viewed live via website, `curl` or websocket.
* Results are instantly reported to github.


### Why kevin?

* Totally simple-stupid
* Configurability and customization
* Fast!
* Self-hostable.


### Setup

The `kevin` component interacts with the outside world.
You need a server for it.

`kevin` starts the VM and invokes `chantal` inside that VM to build your job.


#### Build host

##### Host system (Kevin)

 - Install
  - `python >=3.4`
  - `tornado`
  - `requests`
 - Create user `kevin`
 - Add `kevin` to group `kvm`
 - TODO/optional: create firewall rules to prevent the VM from talking to internals of your network
 - Port 7000 must be unused locally (localhost:7000 is forwarded to :22 in the VM)
 - Create `kevin.conf` from `kevin.conf.example`
 - Create a password-less SSH key with `ssh-keygen`
 - Install the `kevin` Python module (ideally, as a systemd unit or whatever)

##### Guest system (Chantal)

 - You can use the helper scripts in `scripts/vm/` to manage the base image
 - Setup the OS, create user `chantal`
 - Maybe use `scripts/vm/managebaseimage` to edit the base image
 - If your build process includes graphical stuff, setup some autologin stuff.
 - You need `python >=3.4`, `git`, `ssh`, `sudo`
 - It's convenient to have some VNC with e.g. qemu, `tigervnc` or `x11vnc`
 - In `visudo`, give `NOPASSWD: ALL` permissions to `chantal`
 - Setup password-less SSH access (`ssh-copy-id`) for host's `kevin` user to `chantal@guest`


#### Project

 - Create [control file](dist/controlfile.example) `.kevin` in project root
 - Create GitHub webhook:
  - Settings > Webhooks and Services > Add Webhook
  - content type: JSON, URL: `http://your-host:webhook_port`
