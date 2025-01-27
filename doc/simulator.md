Hosting site simulator
======================


To allow development without a test repo e.g. at github,
we provide a simulator that mimics the api of that service.

These are located in `kevin/simulator`.


Example for building `some-repo` with a kevin currently running with
the given config file:

```
python -m kevin.simulator http://github.com/SFTtech/openage projectname /some/kevin.conf github
```

Alternatively, for a local repo on your machine:

```
python -m kevin.simulator --local-repo ~/devel/some-repo/.git projectname /some/kevin.conf github
```


This command delivers a webhook as if somebody had pushed to a repo,
then the simulator waits for kevins status updates etc.
