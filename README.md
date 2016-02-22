# kevin CI

A simple-stupid self-hosted continuous integration service.


### Dafuq?

Kevin is a self-hostable CI daemon to build [pull requests](https://help.github.com/articles/using-pull-requests/) inside your VMs.


It was mainly developed for [openage](http://openage.sft.mx/),
but you can use it for _any_ project!

Kevin can create doc files, bundle software, run tests, make screenshots,
run any container/VM.

Requires [Python >=3.4](https://www.python.org/)
and [tornado](http://www.tornadoweb.org/).


### How?

* Your running `kevin` daemon is notified by a github webhook.
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

You have to set up 3 things: **Kevin**, **Falk** and **Chantal**.

How? Lurk into [our setup guide](doc/setup.md).


### TODO

* `mandy` webinterface in [EmberJS](http://emberjs.com/)
* `rolf` command line client
* Parallel build/job processing with `asyncio`
* More actions: Email, IRC, ...
* More hosting services:
  [GitLab](https://gitlab.com/),
  [Phabricator](http://phabricator.org/),
  [Gogs](https://gogs.io/),
  [BitBucket](https://bitbucket.org/),
  ...
* Support for more containers


### Contact

If you have questions, suggestions, encounter any problem,
please join our IRC channel and ask!

```
irc.freenode.net #sfttech
```

Of course, create [issues](https://github.com/SFTtech/kevin/issues)
and [pull requests](https://github.com/SFTtech/kevin/pulls).


### License

Released under the **GNU Affero General Public License** version 3 or later,
see [COPYING](COPYING) and [LICENSE](LICENSE) for details.
