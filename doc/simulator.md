Hosting site simulator
======================


To allow development without a test repo e.g. at github,
we provide a simulator that mimics the api of that service.

These are located in `kevin/simulator`.


Example for letting `some-repo` been built by the kevin currently running with
the given config file:

```
python -m kevin.simulator github http://github.com/SFTtech/openage /some/kevin.conf
```

Alternatively, for a local repo on your machine:

```
python -m kevin.simulator --local-repo github ~/devel/some-repo/.git /some/kevin.conf
```


This command delivers a webhook as if somebody had pushed to a repo,
then the simulator waits for kevins status updates etc.
