# Kevin CI

A simple-stupid self-hosted continuous integration service.


### Dafuq?

Kevin is a self-hostable CI daemon to build [pull requests](https://help.github.com/articles/using-pull-requests/) inside temporary containers.


It was mainly developed for [openage](http://github.com/SFTtech/openage/),
but you can use it for _any_ project!

Kevin can create doc files, bundle software, run tests, make screenshots,
end world hunger, calculate the last digits of pi: all in a custom container.

Requires [Python >=3.6](https://www.python.org/),
[tornado >=5.1](http://www.tornadoweb.org/) and [qemu](http://qemu-project.org).


### How?

* Your running `kevin` daemon is notified by a github webhook.
* It spawns a temporary VM for the job.
* The repo is cloned and the build/test steps in `kevinfile` are executed.
* Progress can be viewed live via website, github, `curl` or websocket API.
* Results are instantly reported to github.


### Features

* Makefile-like [control file (`kevinfile`)](etc/kevinfile.example)
  * Directly specify command dependencies of your build
  * Report the step results and timing back to github

* Live-view of build console output
  * See what the machine builds in real-time
  * Store and download resulting files (e.g. releases)

* GitHub pull requests
  * A build is triggered for each new and updated pull request
  * When you push to a currently-in-build branch,
    the previous build is canceled

* File output
  * Let your project generate files and folders
  * They're saved to the static web folder
  * Use it to generate documentation, releases, ...

* Container management
  * Jobs are built in temporary throwaway VMs
  * Easily change and update the base images


### Components

* **Kevin**: Receives triggers and launches the builds
* **Falk**: Provides temporary containers to Kevin
* **Chantal**: Run inside the container to execute the Job
* **Mandy**: Webinterface to view live-results


### Setup

You have to set up 3 things: **Kevin**, **Falk** and **Chantal**.
Optionally, serve the **Mandy** webinterface with any static webserver.

**How?** [Lurk into our setup guide](doc/setup.md).


### TODO

* More actions: Email, Matrix, IRC, ...
* More hosting services:
  * [X] [GitHub](https://github.com/),
  * [ ] [GitLab](https://gitlab.com/),
  * [ ] [Phabricator](http://phabricator.org/),
  * [ ] [Gogs](https://gogs.io/),
  * [ ] [BitBucket](https://bitbucket.org/),
  * [ ] ...
* [Support for more container types](/falk/vm/)
  * [X] [qemu](http://qemu-project.org)
  * [ ] [docker](https://www.docker.com/)
  * [ ] [libvirt](https://libvirt.org/)
  * [ ] [xen](https://www.xenproject.org/)
  * [ ] [nspawn](http://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
  * [ ] [lxc](https://linuxcontainers.org/)
  * [ ] [rkt](https://coreos.com/rkt/docs/latest/)
  * [ ] [clearlinux](https://clearlinux.org/)
  * [ ] ...
* Kevinception: Test Kevin with Kevin


### Contact

If you have questions, suggestions, encounter any problem,
please join our Matrix or IRC channel and ask!

```
#sfttech:matrix.org
irc.freenode.net #sfttech
```

Of course, create [issues](https://github.com/SFTtech/kevin/issues)
and [pull requests](https://github.com/SFTtech/kevin/pulls).


### License

Released under the **GNU Affero General Public License** version 3 or later,
see [COPYING](COPYING) and [LICENSE](LICENSE) for details.
