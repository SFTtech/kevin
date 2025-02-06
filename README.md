# Kevin-CI

Kevin-CI is a self-hosted continuous integration service.

With Kevin you have **maximum-speed builds**, spectacular GitHub integration and the best™ CI experience ever.

Kevin-CI supports [QEMU](https://qemu.org), [LXD](https://canonical.com/lxd) and [Podman](https://podman.io).


### Dafuq?

Kevin is a self-hostable CI daemon to build [pull requests](https://help.github.com/articles/using-pull-requests/) inside temporary containers.


It was mainly developed for [openage](http://github.com/SFTtech/openage/),
but you can use it for _any_ project!

Kevin can create doc files, bundle software, run tests, make screenshots,
end world hunger, calculate the last digits of pi: all in a custom container.

Requires:
- [Python >=3.11](https://www.python.org/)
- [aiohttp](https://aiohttp.org/)
- and some container/vm to run jobs in


### Components

* **Kevin**: Receives triggers and launches the builds
* **Justin**: Provides temporary containers to Kevin
* **Chantal**: Run inside the container to execute the Job
* **Mandy**: Webinterface to view live-results


### How?

* `kevin` is notified by a GitHub webhook
* It spawns a temporary Container/VM from a template to run the job
* The repo is cloned and the build/test steps in `kevinfile` are executed
* Progress can be viewed live via Web-UI, GitHub, `curl` or websocket API
* Results are instantly reported to GitHub


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


### Setup

**How?** [Lurk into our setup guide](doc/setup.md).


### TODO

* More actions: Email, Matrix, IRC, ...
* More hosting services:
  * [X] [GitHub](https://github.com/),
  * [ ] [GitLab](https://gitlab.com/),
  * [ ] ...
* [Support for more container/vm types](/justin/machine/)
  * [X] [qemu](http://qemu-project.org)
  * [X] [podman](https://podman.io/)
  * [x] [lxd](https://linuxcontainers.org/lxd)
  * [ ] [docker](https://www.docker.com/)
  * [ ] [libvirt](https://libvirt.org/)
  * [ ] [xen](https://www.xenproject.org/)
  * [ ] [nspawn](http://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
  * [ ] ...
* Kevinception: Test Kevin with Kevin


### Contact

If you have questions, suggestions, encounter any problem,
please join our [Matrix channel](https://matrix.to/#/#sfttech:matrix.org) and ask!

Of course, create [issues](https://github.com/SFTtech/kevin-ci/issues)
and [pull requests](https://github.com/SFTtech/kevin-ci/pulls).


### License

Released under the **GNU Affero General Public License** version 3 or later,
see [COPYING](COPYING) and [LICENSE](LICENSE) for details.
