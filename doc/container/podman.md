Podman Containers
=================

In order to make falk be able to manage podman containers you need to provide it with
a suitable container image. This image must run a ssh server such that the buildsystem
is able to execute the build steps via remote commands.

A minimal example for a Dockerfile building a usable debian sid image would be

```dockerfile
FROM debian:sid

ARG authorized_keys
ENV AUTHORIZED_KEYS=$authorized_keys

# install packages
RUN apt-get update
RUN apt-get install -y git openssh-server

# setup user and ssh
RUN useradd -m chantal
RUN mkdir /home/chantal/.ssh
RUN echo "$AUTHORIZED_KEYS" > /home/chantal/.ssh/authorized_keys
RUN chmod 700 /home/chantal/.ssh
RUN chmod 644 /home/chantal/.ssh/authorized_keys
RUN chown -R chantal:root /home/chantal/.ssh
RUN mkdir -p /var/run/sshd /run/sshd

# SSH login fix. Otherwise user is kicked off after login
RUN sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd

ENV NOTVISIBLE "in users profile"
RUN echo "export VISIBLE=now" >> /etc/profile

RUN cat /etc/ssh/ssh_host_ed25519_key.pub

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-p", "22"]
```

Podman needs its [own preparation](https://github.com/containers/libpod/tree/master/docs/tutorials):
* e.g. the subuids and subgids described in [their rootless guide](https://github.com/containers/libpod/blob/master/docs/tutorials/rootless_tutorial.md).

This image would then be built as the user running the falk daemon like
```shell script
su <falk-user> podman build -t <your-image-tag> --build-arg authorized_keys="<kevin-ci-user ssh key>" .
```
Instead of passing the build users (the one running the kevin deamon) ssh key via container build args it is
also possible to just copy the public key from the host system.
