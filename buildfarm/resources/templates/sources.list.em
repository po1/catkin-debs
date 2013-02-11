deb @(repo) @(distro) main @(arch != 'armhf' or distro != 'wheezy' ? "restricted universe multiverse")
deb-src @(repo) @(distro) main @(arch != 'armhf' or distro != 'wheezy' ? "restricted universe multiverse")

## Major bug fix updates produced after the final release of the
## distribution.
@(arch == 'armhf' and distro == 'wheezy' ? "#")deb @(repo) @(distro)-updates main restricted universe multiverse
@(arch == 'armhf' and distro == 'wheezy' ? "#")deb-src @(repo) @(distro)-updates main restricted universe multiverse
