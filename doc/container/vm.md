VM container setup
==================

Use this guide if your container will run as a real virtual machine (QEMU, ...).

You'll set up the system that will process a build job.
Because of that, you shold prepare it in such a way this VM is suited well for building your job!

Basically, you'll have a full system installation (Linux, ...) that is then reached and controlled via SSH and Python.

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
- Setup password-less SSH access (`ssh-copy-id`) for above `kevin` user to `chantal@container_vm`
  - Add `kevin`'s `id_rsa.pub` into `~chantal/.ssh/authorized_keys`
- Store the contents of the container's `/etc/ssh/ssh_host_ed25519_key.pub`
  to the `justin.conf` so the key for this VM can be verified
- **Set up the container** in the way you'd like to test your project
  - If your build involves graphical things, you could set up `tigervnc` or `x11vnc`
