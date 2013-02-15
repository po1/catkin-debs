#!/usr/bin/env python

from __future__ import print_function
import argparse
import os
import sys
import tempfile

from buildfarm import jenkins_support, release_jobs

import rospkg.distro

from buildfarm.release_jobs import get_targets, debianize_package_name
from buildfarm.release_jobs import JobParams, PackageParams
from rosdistro.rosdistro import RosDistro
from rosdep2 import rospack

def parse_options():
    parser = argparse.ArgumentParser(
             description='Create a set of jenkins jobs '
             'for source debs and binary debs for a catkin package.')
    parser.add_argument('--fqdn', dest='fqdn',
           help='The source repo to push to, fully qualified something...',
           default='50.28.27.175')
    parser.add_argument(dest='rosdistro',
           help='The ros distro. fuerte, groovy, hydro, ...')
    parser.add_argument('--distros', nargs='+',
           help='A list of debian distros. Default: %(default)s',
           default=[])
    parser.add_argument('--arches', nargs='+',
           help='A list of debian architectures. Default: %(default)s',
           default=['i386', 'amd64'])
    parser.add_argument('--commit', dest='commit',
           help='Really?', action='store_true', default=False)
    parser.add_argument('--delete', dest='delete',
           help='Delete extra jobs', action='store_true', default=False)
    parser.add_argument('--no-update', dest='skip_update',
           help='Assume packages have already been downloaded', action='store_true', default=False)
    parser.add_argument('--wet-only', dest='wet_only',
           help='Only setup wet jobs', action='store_true', default=False)
    parser.add_argument('--repo-workspace', action='store',
           help='A directory into which all the repositories will be checked out into.')
    parser.add_argument('--repos', nargs='+',
           help='A list of repository (or stack) names to create. Default: creates all')
    parser.add_argument('--rosdistro', dest='rosdist_rep', default='https://raw.github.com/ros/rosdistro/master/',
            help='The base path to a rosdistro repository. Default: %(default)s')
    args = parser.parse_args()
    if args.repos and args.delete:
        parser.error('A set of repos to create can not be combined with the --delete option.')
    return args


def doit(job_params, dry_maintainers, packages, rosdist_rep,
         wet_only=False, commit = False, delete_extra_jobs = False, whitelist_repos = None):

    jenkins_instance = None
    if commit or delete_extra_jobs:
        jenkins_config = jenkins_support.load_server_config_file(jenkins_support.get_default_catkin_debs_config())
        jenkins_instance = jenkins_support.JenkinsConfig_to_handle(jenkins_config)

    rosdistro = job_params.rosdistro
    rd = job_params.rd


    # We take the intersection of repo-specific targets with default
    # targets.
    results = {}

    for repo_name in sorted(rd.get_repositories()):
        if whitelist_repos and repo_name not in whitelist_repos:
            continue

        r = rd.get_repository(repo_name)

        print ('Configuring WET repo "%s" at "%s" for "%s"' % (r.name, r.url, job_params.distros))
        p_list = [p.name for p in r.packages]
        for p in sorted(p_list):
            if not r.version:
                print('- skipping "%s" since version is null' % p)
                continue
            pkg_name = debianize_package_name(rosdistro, p)
            maintainers = rd.get_maintainers(p)
            pp = PackageParams(package_name=pkg_name,
                               package=packages[p],
                               release_uri=r.url,
                               short_package_name=p,
                               maintainers=maintainers)

            results[pkg_name] = release_jobs.doit(job_params=job_params,
                                                  pkg_params=pp,
                                                  commit=commit,
                                                  jenkins_instance=jenkins_instance)
            #time.sleep(1)
            #print ('individual results', results[pkg_name])

    if wet_only:
        print ("wet only selected, skipping dry and delete")
        return results

    default_distros = job_params.distros
    target_arches = list(set([x for d in default_distros for x in job_params.arches[d]]))
    rosdistro = job_params.rosdistro
    jobs_graph = job_params.jobgraph

    if rosdistro == 'backports':
        print ("No dry backports support")
        return results

    if rosdistro == 'fuerte':
        packages_for_sync = 300
    elif rosdistro == 'groovy':
        packages_for_sync = 500
    elif rosdistro == 'hydro':
        packages_for_sync = 60
    else:
        packages_for_sync = 10000

    #dry stacks
    # dry dependencies
    d = rospkg.distro.load_distro(rospkg.distro.distro_uri(rosdistro))

    for s in sorted(d.stacks.iterkeys()):
        if whitelist_repos and s not in whitelist_repos:
            continue
        print ("Configuring DRY job [%s]" % s)
        if not d.stacks[s].version:
            print('- skipping "%s" since version is null' % s)
            continue
        results[debianize_package_name(rd.name, s)] = release_jobs.dry_doit(s, dry_maintainers[s], default_distros, target_arches, rosdistro, jobgraph=jobs_graph, commit=commit, jenkins_instance=jenkins_instance, packages_for_sync=packages_for_sync)
        #time.sleep(1)

    # special metapackages job
    if not whitelist_repos or 'metapackages' in whitelist_repos:
        results[debianize_package_name(rd.name, 'metapackages')] = release_jobs.dry_doit('metapackages', [], default_distros, target_arches, rosdistro, jobgraph=jobs_graph, commit=commit, jenkins_instance=jenkins_instance, packages_for_sync=packages_for_sync)

    if delete_extra_jobs:
        assert(not whitelist_repos)
        # clean up extra jobs
        configured_jobs = set()

        for jobs in results.values():
            release_jobs.summarize_results(*jobs)
            for e in jobs:
                configured_jobs.update(set(e))

        existing_jobs = set([j['name'] for j in jenkins_instance.get_jobs()])
        relevant_jobs = existing_jobs - configured_jobs
        relevant_jobs = [j for j in relevant_jobs if rosdistro in j and ('_sourcedeb' in j or '_binarydeb' in j)]

        for j in relevant_jobs:
            print('Job "%s" detected as extra' % j)
            if commit:
                jenkins_instance.delete_job(j)
                print('Deleted job "%s"' % j)

    return results

def get_dependencies(rd, packages):
    dependencies = {}
    v = rospack.init_rospack_interface()
    for p in packages:
        deps = rd.get_depends(p)
        dp = debianize_package_name(rd.name, p)
        dependencies[dp] = []
        combined_deps = set(deps['build']) | set(deps['run'])
        for d in combined_deps:
            if not rospack.is_system_dependency(v, d):
                dependencies[dp].append(debianize_package_name(rd.name, d))
    return dependencies


if __name__ == '__main__':
    args = parse_options()

    repo = 'http://%s/repos/building' % args.fqdn

    print('Loading rosdistro %s' % args.rosdistro)

    rd = RosDistro(args.rosdistro, rosdist_rep=args.rosdist_rep)

    workspace = args.repo_workspace
    if not workspace:
        workspace = os.path.join(tempfile.gettempdir(), 'repo-workspace-%s' % args.rosdistro)

    if args.rosdistro != 'fuerte':
        packages = rd.get_packages()
        dependencies = get_dependencies(rd, packages)
    else:
        from buildfarm import dependency_walker_fuerte
        stacks = dependency_walker_fuerte.get_stacks(workspace, rd.distro_file.repositories, args.rosdistro, skip_update=args.skip_update)
        dependencies = dependency_walker_fuerte.get_dependencies(args.rosdistro, stacks)
        packages = stacks

    release_jobs.check_for_circular_dependencies(dependencies)

    # even for wet_only the dry packages need to be consider, else they are not added as downstream dependencies for the wet jobs
    stack_depends, dry_maintainers = release_jobs.dry_get_stack_dependencies(args.rosdistro)
    dry_jobgraph = release_jobs.dry_generate_jobgraph(args.rosdistro, dependencies, stack_depends)

    combined_jobgraph = {}
    for k, v in dependencies.iteritems():
        combined_jobgraph[k] = v
    for k, v in dry_jobgraph.iteritems():
        combined_jobgraph[k] = v

    # setup a job triggered by all other debjobs
    combined_jobgraph[debianize_package_name(args.rosdistro, 'metapackages')] = combined_jobgraph.keys()

    targets = get_targets(rd, args.distros, args.arches)
    jp = JobParams(rosdistro=args.rosdistro,
                   distros=targets.keys(),
                   arches=targets,
                   fqdn=args.fqdn,
                   jobgraph=combined_jobgraph,
                   rosdist_rep=args.rosdist_rep,
                   rd_object=rd)

    results_map = doit(job_params=jp,
                       packages=packages,
                       dry_maintainers=dry_maintainers,
                       commit=args.commit,
                       wet_only=args.wet_only,
                       rosdist_rep=args.rosdist_rep,
                       delete_extra_jobs=args.delete,
                       whitelist_repos=args.repos)

    if not args.commit:
        print('This was not pushed to the server.  If you want to do so use "--commit" to do it for real.')
