Ideas
=====


These are things that might come in handy for kevin someday.
If you'd like to start implementing one of those, you can
ask on our IRC (freenode.net #sfttech) for assistance.


#### Job finish actions

* Notifications via IRC, email, ...


#### Local development

* Prepare kevin for your usage on your local machine
* Any project can be built with a simple command only (`kevin run`?)
* This doesn't even have to take place in a VM since it's your code

#### More services

* `gitolite`: git-post-receive hook to trigger kevin
* Any other github-like or git-related hosting service


#### More containers

* Implement the stubs in the `justin/machine` folder to support other machines


#### VM management

* Direct management console via control connection
* Ressource limitations (e.g. vm memory, max running vms)


#### Compiler interaction

* Parse compiler output (asan, errors, warnings, ...)
* Project setting: `compilers=gcc,clang,ghc, ...`, `parse=True`
* Perform some actions
  * Directly comment offending line on github
  * Collect statistics, ...
