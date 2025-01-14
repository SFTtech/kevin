LXD Containers
==============

Falk can manage [LXD](https://linuxcontainers.org/lxd/) containers.
The launched container must run a SSH server such that the buildsystem is able to execute the build steps via remote commands.


## Image Creation

A minimal example to create such a container follows.
If you execute this as a script, it's something like a `Dockerfile`, just without the Docker stuff :)

```python
import subprocess
import shlex

def ex(cmd, output=False):
    print(f"# {cmd}")
    cmd = shlex.split(cmd)
    if output:
        return subprocess.check_output(cmd)
    else:
        return subprocess.check_call(cmd)

# this key can then log into the containers
with open("/home/kevin/.ssh/id_rsa.pub") as hdl:
    authorized_keys = hdl.read()

# this is the container we build the template in
template_container = "kevincitemplate"

# after the template container is done, store the filesystem as this image
template_image = "kevin/debian-sid"

# base the image on debian sid
ex(f"lxc launch images:debian/sid {template_container}")

# install packages
ex(f"lxc exec {template_container} -- apt-get update")
ex(f"lxc exec {template_container} -- apt-get install -y git openssh-server")

# setup user and ssh
ex(f"lxc exec {template_container} -- useradd -m chantal")
ex(f"lxc exec {template_container} -- mkdir /home/chantal/.ssh")
ex(f"lxc exec {template_container} -- sh -c \"echo '{authorized_keys}' > /home/chantal/.ssh/authorized_keys\"")
ex(f"lxc exec {template_container} -- chmod 700 /home/chantal/.ssh")
ex(f"lxc exec {template_container} -- chmod 644 /home/chantal/.ssh/authorized_keys")
ex(f"lxc exec {template_container} -- chown -R chantal:root /home/chantal/.ssh")
ex(f"lxc exec {template_container} -- systemctl enable --now ssh")

print("public key of the container's ssh daemon:")
print(ex(f"lxc exec {template_container} -- cat /etc/ssh/ssh_host_ed25519_key.pub", output=True).decode())

## now that ssh is available, you could do further setup steps via Ansible, for example.

# stop the container and convert it to an image
ex(f"lxc stop {template_container}")
ex(f"lxc publish {template_container} --alias '{template_image}'")
ex(f"lxc rm {template_container}")
```

Running this script creates you a container image (named after `template_image`), which Falk can then use to spawn temporary CI containers from.


## Run a Temporary CI Container

You can test-launch such a temporary container with:

```bash
lxc launch kevin/debian-sid lolkevintest --ephemeral

# log into the container
lxc exec lolkevintest -- bash
```

When it's turned off (`poweroff` in the container or with `lxc stop lolkevintest` on the host), it's automatically deleted!


## Container Image Updating

You will see the image we created here:
```bash
lxc image ls

lxc image info kevin/debian-sid
```

There you can also remove it (`lxc image delete kevin/debian-sid`) and create a new one with the script above.

To update the image without deleting it, do these steps:
```bash
# create non-ephemeral container to update the image
lxc launch kevin/debian-sid tmp-imageupdate

# do the update steps interactively (or script the steps like above)
lxc exec tmp-imageupdate -- bash

# once you're done, stop the container so we can export it as image again
lxc stop tmp-imageupdate
lxc publish tmp-imageupdate --alias kevin/debian-sid
lxc rm tmp-imageupdate

# you can clean up the old images in the image list
lxc image ls
lxc image delete <image-fingerprint>
```
