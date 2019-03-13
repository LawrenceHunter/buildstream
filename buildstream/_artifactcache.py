#
#  Copyright (C) 2017-2018 Codethink Limited
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library. If not, see <http://www.gnu.org/licenses/>.
#
#  Authors:
#        Tristan Maat <tristan.maat@codethink.co.uk>

import multiprocessing
import os
from collections.abc import Mapping

from .types import _KeyStrength
from ._exceptions import ArtifactError, CASError
from ._message import Message, MessageType
from . import utils
from . import _yaml

from ._cas import CASRemote, CASRemoteSpec, CASCacheUsage
from .storage._casbaseddirectory import CasBasedDirectory


CACHE_SIZE_FILE = "cache_size"


# An ArtifactCacheSpec holds the user configuration for a single remote
# artifact cache.
#
# Args:
#     url (str): Location of the remote artifact cache
#     push (bool): Whether we should attempt to push artifacts to this cache,
#                  in addition to pulling from it.
#
class ArtifactCacheSpec(CASRemoteSpec):
    pass


# An ArtifactCache manages artifacts.
#
# Args:
#     context (Context): The BuildStream context
#
class ArtifactCache():
    def __init__(self, context):
        self.context = context

        self.cas = context.get_cascache()
        self.casquota = context.get_casquota()
        self.casquota._calculate_cache_quota()

        self.global_remote_specs = []
        self.project_remote_specs = {}

        self._required_elements = set()       # The elements required for this session

        self._remotes_setup = False           # Check to prevent double-setup of remotes

        # Per-project list of _CASRemote instances.
        self._remotes = {}

        self._has_fetch_remotes = False
        self._has_push_remotes = False

    # setup_remotes():
    #
    # Sets up which remotes to use
    #
    # Args:
    #    use_config (bool): Whether to use project configuration
    #    remote_url (str): Remote artifact cache URL
    #
    # This requires that all of the projects which are to be processed in the session
    # have already been loaded and are observable in the Context.
    #
    def setup_remotes(self, *, use_config=False, remote_url=None):

        # Ensure we do not double-initialise since this can be expensive
        assert not self._remotes_setup
        self._remotes_setup = True

        # Initialize remote artifact caches. We allow the commandline to override
        # the user config in some cases (for example `bst artifact push --remote=...`).
        has_remote_caches = False
        if remote_url:
            self._set_remotes([ArtifactCacheSpec(remote_url, push=True)])
            has_remote_caches = True
        if use_config:
            for project in self.context.get_projects():
                artifact_caches = _configured_remote_artifact_cache_specs(self.context, project)
                if artifact_caches:  # artifact_caches is a list of ArtifactCacheSpec instances
                    self._set_remotes(artifact_caches, project=project)
                    has_remote_caches = True
        if has_remote_caches:
            self._initialize_remotes()

    # specs_from_config_node()
    #
    # Parses the configuration of remote artifact caches from a config block.
    #
    # Args:
    #   config_node (dict): The config block, which may contain the 'artifacts' key
    #   basedir (str): The base directory for relative paths
    #
    # Returns:
    #   A list of ArtifactCacheSpec instances.
    #
    # Raises:
    #   LoadError, if the config block contains invalid keys.
    #
    @staticmethod
    def specs_from_config_node(config_node, basedir=None):
        cache_specs = []

        artifacts = config_node.get('artifacts', [])
        if isinstance(artifacts, Mapping):
            cache_specs.append(ArtifactCacheSpec._new_from_config_node(artifacts, basedir))
        elif isinstance(artifacts, list):
            for spec_node in artifacts:
                cache_specs.append(ArtifactCacheSpec._new_from_config_node(spec_node, basedir))
        else:
            provenance = _yaml.node_get_provenance(config_node, key='artifacts')
            raise _yaml.LoadError(_yaml.LoadErrorReason.INVALID_DATA,
                                  "%s: 'artifacts' must be a single 'url:' mapping, or a list of mappings" %
                                  (str(provenance)))
        return cache_specs

    # mark_required_elements():
    #
    # Mark elements whose artifacts are required for the current run.
    #
    # Artifacts whose elements are in this list will be locked by the artifact
    # cache and not touched for the duration of the current pipeline.
    #
    # Args:
    #     elements (iterable): A set of elements to mark as required
    #
    def mark_required_elements(self, elements):

        # We risk calling this function with a generator, so we
        # better consume it first.
        #
        elements = list(elements)

        # Mark the elements as required. We cannot know that we know the
        # cache keys yet, so we only check that later when deleting.
        #
        self._required_elements.update(elements)

        # For the cache keys which were resolved so far, we bump
        # the mtime of them.
        #
        # This is just in case we have concurrent instances of
        # BuildStream running with the same artifact cache, it will
        # reduce the likelyhood of one instance deleting artifacts
        # which are required by the other.
        for element in elements:
            strong_key = element._get_cache_key(strength=_KeyStrength.STRONG)
            weak_key = element._get_cache_key(strength=_KeyStrength.WEAK)
            for key in (strong_key, weak_key):
                if key:
                    try:
                        ref = element.get_artifact_name(key)

                        self.cas.update_mtime(ref)
                    except CASError:
                        pass

    # clean():
    #
    # Clean the artifact cache as much as possible.
    #
    # Args:
    #    progress (callable): A callback to call when a ref is removed
    #
    # Returns:
    #    (int): The size of the cache after having cleaned up
    #
    def clean(self, progress=None):
        artifacts = self.list_artifacts()
        context = self.context

        # Some accumulative statistics
        removed_ref_count = 0
        space_saved = 0

        # Start off with an announcement with as much info as possible
        volume_size, volume_avail = self.casquota._get_cache_volume_size()
        self._message(MessageType.STATUS, "Starting cache cleanup",
                      detail=("Elements required by the current build plan: {}\n" +
                              "User specified quota: {} ({})\n" +
                              "Cache usage: {}\n" +
                              "Cache volume: {} total, {} available")
                      .format(len(self._required_elements),
                              context.config_cache_quota,
                              utils._pretty_size(self.casquota._cache_quota, dec_places=2),
                              utils._pretty_size(self.casquota.get_cache_size(), dec_places=2),
                              utils._pretty_size(volume_size, dec_places=2),
                              utils._pretty_size(volume_avail, dec_places=2)))

        # Build a set of the cache keys which are required
        # based on the required elements at cleanup time
        #
        # We lock both strong and weak keys - deleting one but not the
        # other won't save space, but would be a user inconvenience.
        required_artifacts = set()
        for element in self._required_elements:
            required_artifacts.update([
                element._get_cache_key(strength=_KeyStrength.STRONG),
                element._get_cache_key(strength=_KeyStrength.WEAK)
            ])

        # Do a real computation of the cache size once, just in case
        self.casquota.compute_cache_size()
        usage = CASCacheUsage(self.casquota)
        self._message(MessageType.STATUS, "Cache usage recomputed: {}".format(usage))

        while self.casquota.get_cache_size() >= self.casquota._cache_lower_threshold:
            try:
                to_remove = artifacts.pop(0)
            except IndexError:
                # If too many artifacts are required, and we therefore
                # can't remove them, we have to abort the build.
                #
                # FIXME: Asking the user what to do may be neater
                #
                default_conf = os.path.join(os.environ['XDG_CONFIG_HOME'],
                                            'buildstream.conf')
                detail = ("Aborted after removing {} refs and saving {} disk space.\n"
                          "The remaining {} in the cache is required by the {} elements in your build plan\n\n"
                          "There is not enough space to complete the build.\n"
                          "Please increase the cache-quota in {} and/or make more disk space."
                          .format(removed_ref_count,
                                  utils._pretty_size(space_saved, dec_places=2),
                                  utils._pretty_size(self.casquota.get_cache_size(), dec_places=2),
                                  len(self._required_elements),
                                  (context.config_origin or default_conf)))

                if self.full():
                    raise ArtifactError("Cache too full. Aborting.",
                                        detail=detail,
                                        reason="cache-too-full")
                else:
                    break

            key = to_remove.rpartition('/')[2]
            if key not in required_artifacts:

                # Remove the actual artifact, if it's not required.
                size = self.remove(to_remove)

                removed_ref_count += 1
                space_saved += size

                self._message(MessageType.STATUS,
                              "Freed {: <7} {}".format(
                                  utils._pretty_size(size, dec_places=2),
                                  to_remove))

                # Remove the size from the removed size
                self.casquota.set_cache_size(self.casquota._cache_size - size)

                # User callback
                #
                # Currently this process is fairly slow, but we should
                # think about throttling this progress() callback if this
                # becomes too intense.
                if progress:
                    progress()

        # Informational message about the side effects of the cleanup
        self._message(MessageType.INFO, "Cleanup completed",
                      detail=("Removed {} refs and saving {} disk space.\n" +
                              "Cache usage is now: {}")
                      .format(removed_ref_count,
                              utils._pretty_size(space_saved, dec_places=2),
                              utils._pretty_size(self.casquota.get_cache_size(), dec_places=2)))

        return self.casquota.get_cache_size()

    def full(self):
        return self.casquota.full()

    # add_artifact_size()
    #
    # Adds the reported size of a newly cached artifact to the
    # overall estimated size.
    #
    # Args:
    #     artifact_size (int): The size to add.
    #
    def add_artifact_size(self, artifact_size):
        cache_size = self.casquota.get_cache_size()
        cache_size += artifact_size

        self.casquota.set_cache_size(cache_size)

    # preflight():
    #
    # Preflight check.
    #
    def preflight(self):
        self.cas.preflight()

    # initialize_remotes():
    #
    # This will contact each remote cache.
    #
    # Args:
    #     on_failure (callable): Called if we fail to contact one of the caches.
    #
    def initialize_remotes(self, *, on_failure=None):
        remote_specs = list(self.global_remote_specs)

        for project in self.project_remote_specs:
            remote_specs += self.project_remote_specs[project]

        remote_specs = list(utils._deduplicate(remote_specs))

        remotes = {}
        q = multiprocessing.Queue()
        for remote_spec in remote_specs:

            error = CASRemote.check_remote(remote_spec, q)

            if error and on_failure:
                on_failure(remote_spec.url, error)
            elif error:
                raise ArtifactError(error)
            else:
                self._has_fetch_remotes = True
                if remote_spec.push:
                    self._has_push_remotes = True

                remotes[remote_spec.url] = CASRemote(remote_spec)

        for project in self.context.get_projects():
            remote_specs = self.global_remote_specs
            if project in self.project_remote_specs:
                remote_specs = list(utils._deduplicate(remote_specs + self.project_remote_specs[project]))

            project_remotes = []

            for remote_spec in remote_specs:
                # Errors are already handled in the loop above,
                # skip unreachable remotes here.
                if remote_spec.url not in remotes:
                    continue

                remote = remotes[remote_spec.url]
                project_remotes.append(remote)

            self._remotes[project] = project_remotes

    # contains():
    #
    # Check whether the artifact for the specified Element is already available
    # in the local artifact cache.
    #
    # Args:
    #     element (Element): The Element to check
    #     key (str): The cache key to use
    #
    # Returns: True if the artifact is in the cache, False otherwise
    #
    def contains(self, element, key):
        ref = element.get_artifact_name(key)

        return self.cas.contains(ref)

    # contains_subdir_artifact():
    #
    # Check whether an artifact element contains a digest for a subdir
    # which is populated in the cache, i.e non dangling.
    #
    # Args:
    #     element (Element): The Element to check
    #     key (str): The cache key to use
    #     subdir (str): The subdir to check
    #
    # Returns: True if the subdir exists & is populated in the cache, False otherwise
    #
    def contains_subdir_artifact(self, element, key, subdir):
        ref = element.get_artifact_name(key)
        return self.cas.contains_subdir_artifact(ref, subdir)

    # list_artifacts():
    #
    # List artifacts in this cache in LRU order.
    #
    # Args:
    #     glob (str): An option glob expression to be used to list artifacts satisfying the glob
    #
    # Returns:
    #     ([str]) - A list of artifact names as generated in LRU order
    #
    def list_artifacts(self, *, glob=None):
        return self.cas.list_refs(glob=glob)

    # remove():
    #
    # Removes the artifact for the specified ref from the local
    # artifact cache.
    #
    # Args:
    #     ref (artifact_name): The name of the artifact to remove (as
    #                          generated by `Element.get_artifact_name`)
    #     defer_prune (bool): Optionally declare whether pruning should
    #                         occur immediately after the ref is removed.
    #
    # Returns:
    #    (int): The amount of space recovered in the cache, in bytes
    #
    def remove(self, ref, *, defer_prune=False):
        return self.cas.remove(ref, defer_prune=defer_prune)

    # prune():
    #
    # Prune the artifact cache of unreachable refs
    #
    def prune(self):
        return self.cas.prune()

    # get_artifact_directory():
    #
    # Get virtual directory for cached artifact of the specified Element.
    #
    # Assumes artifact has previously been fetched or committed.
    #
    # Args:
    #     element (Element): The Element to extract
    #     key (str): The cache key to use
    #
    # Raises:
    #     ArtifactError: In cases there was an OSError, or if the artifact
    #                    did not exist.
    #
    # Returns: virtual directory object
    #
    def get_artifact_directory(self, element, key):
        ref = element.get_artifact_name(key)
        digest = self.cas.resolve_ref(ref, update_mtime=True)
        return CasBasedDirectory(self.cas, digest=digest)

    # commit():
    #
    # Commit built artifact to cache.
    #
    # Args:
    #     element (Element): The Element commit an artifact for
    #     content (Directory): The element's content directory
    #     keys (list): The cache keys to use
    #
    def commit(self, element, content, keys):
        refs = [element.get_artifact_name(key) for key in keys]

        tree = content._get_digest()

        for ref in refs:
            self.cas.set_ref(ref, tree)

    # diff():
    #
    # Return a list of files that have been added or modified between
    # the artifacts described by key_a and key_b.
    #
    # Args:
    #     element (Element): The element whose artifacts to compare
    #     key_a (str): The first artifact key
    #     key_b (str): The second artifact key
    #     subdir (str): A subdirectory to limit the comparison to
    #
    def diff(self, element, key_a, key_b, *, subdir=None):
        ref_a = element.get_artifact_name(key_a)
        ref_b = element.get_artifact_name(key_b)

        return self.cas.diff(ref_a, ref_b, subdir=subdir)

    # has_fetch_remotes():
    #
    # Check whether any remote repositories are available for fetching.
    #
    # Args:
    #     element (Element): The Element to check
    #
    # Returns: True if any remote repositories are configured, False otherwise
    #
    def has_fetch_remotes(self, *, element=None):
        if not self._has_fetch_remotes:
            # No project has fetch remotes
            return False
        elif element is None:
            # At least one (sub)project has fetch remotes
            return True
        else:
            # Check whether the specified element's project has fetch remotes
            remotes_for_project = self._remotes[element._get_project()]
            return bool(remotes_for_project)

    # has_push_remotes():
    #
    # Check whether any remote repositories are available for pushing.
    #
    # Args:
    #     element (Element): The Element to check
    #
    # Returns: True if any remote repository is configured, False otherwise
    #
    def has_push_remotes(self, *, element=None):
        if not self._has_push_remotes:
            # No project has push remotes
            return False
        elif element is None:
            # At least one (sub)project has push remotes
            return True
        else:
            # Check whether the specified element's project has push remotes
            remotes_for_project = self._remotes[element._get_project()]
            return any(remote.spec.push for remote in remotes_for_project)

    # push():
    #
    # Push committed artifact to remote repository.
    #
    # Args:
    #     element (Element): The Element whose artifact is to be pushed
    #     keys (list): The cache keys to use
    #
    # Returns:
    #   (bool): True if any remote was updated, False if no pushes were required
    #
    # Raises:
    #   (ArtifactError): if there was an error
    #
    def push(self, element, keys):
        refs = [element.get_artifact_name(key) for key in list(keys)]

        project = element._get_project()

        push_remotes = [r for r in self._remotes[project] if r.spec.push]

        pushed = False

        for remote in push_remotes:
            remote.init()
            display_key = element._get_brief_display_key()
            element.status("Pushing artifact {} -> {}".format(display_key, remote.spec.url))

            if self.cas.push(refs, remote):
                element.info("Pushed artifact {} -> {}".format(display_key, remote.spec.url))
                pushed = True
            else:
                element.info("Remote ({}) already has {} cached".format(
                    remote.spec.url, element._get_brief_display_key()
                ))

        return pushed

    # pull():
    #
    # Pull artifact from one of the configured remote repositories.
    #
    # Args:
    #     element (Element): The Element whose artifact is to be fetched
    #     key (str): The cache key to use
    #     progress (callable): The progress callback, if any
    #     subdir (str): The optional specific subdir to pull
    #     excluded_subdirs (list): The optional list of subdirs to not pull
    #
    # Returns:
    #   (bool): True if pull was successful, False if artifact was not available
    #
    def pull(self, element, key, *, progress=None, subdir=None, excluded_subdirs=None):
        ref = element.get_artifact_name(key)

        project = element._get_project()

        for remote in self._remotes[project]:
            try:
                display_key = element._get_brief_display_key()
                element.status("Pulling artifact {} <- {}".format(display_key, remote.spec.url))

                if self.cas.pull(ref, remote, progress=progress, subdir=subdir, excluded_subdirs=excluded_subdirs):
                    element.info("Pulled artifact {} <- {}".format(display_key, remote.spec.url))
                    # no need to pull from additional remotes
                    return True
                else:
                    element.info("Remote ({}) does not have {} cached".format(
                        remote.spec.url, element._get_brief_display_key()
                    ))

            except CASError as e:
                raise ArtifactError("Failed to pull artifact {}: {}".format(
                    element._get_brief_display_key(), e)) from e

        return False

    # pull_tree():
    #
    # Pull a single Tree rather than an artifact.
    # Does not update local refs.
    #
    # Args:
    #     project (Project): The current project
    #     digest (Digest): The digest of the tree
    #
    def pull_tree(self, project, digest):
        for remote in self._remotes[project]:
            digest = self.cas.pull_tree(remote, digest)

            if digest:
                # no need to pull from additional remotes
                return digest

        return None

    # push_directory():
    #
    # Push the given virtual directory to all remotes.
    #
    # Args:
    #     project (Project): The current project
    #     directory (Directory): A virtual directory object to push.
    #
    # Raises:
    #     (ArtifactError): if there was an error
    #
    def push_directory(self, project, directory):
        if self._has_push_remotes:
            push_remotes = [r for r in self._remotes[project] if r.spec.push]
        else:
            push_remotes = []

        if not push_remotes:
            raise ArtifactError("push_directory was called, but no remote artifact " +
                                "servers are configured as push remotes.")

        for remote in push_remotes:
            self.cas.push_directory(remote, directory)

    # push_message():
    #
    # Push the given protobuf message to all remotes.
    #
    # Args:
    #     project (Project): The current project
    #     message (Message): A protobuf message to push.
    #
    # Raises:
    #     (ArtifactError): if there was an error
    #
    def push_message(self, project, message):

        if self._has_push_remotes:
            push_remotes = [r for r in self._remotes[project] if r.spec.push]
        else:
            push_remotes = []

        if not push_remotes:
            raise ArtifactError("push_message was called, but no remote artifact " +
                                "servers are configured as push remotes.")

        for remote in push_remotes:
            message_digest = remote.push_message(message)

        return message_digest

    # link_key():
    #
    # Add a key for an existing artifact.
    #
    # Args:
    #     element (Element): The Element whose artifact is to be linked
    #     oldkey (str): An existing cache key for the artifact
    #     newkey (str): A new cache key for the artifact
    #
    def link_key(self, element, oldkey, newkey):
        oldref = element.get_artifact_name(oldkey)
        newref = element.get_artifact_name(newkey)

        self.cas.link_ref(oldref, newref)

    # get_artifact_logs():
    #
    # Get the logs of an existing artifact
    #
    # Args:
    #     ref (str): The ref of the artifact
    #
    # Returns:
    #     logsdir (CasBasedDirectory): A CasBasedDirectory containing the artifact's logs
    #
    def get_artifact_logs(self, ref):
        cache_id = self.cas.resolve_ref(ref, update_mtime=True)
        vdir = CasBasedDirectory(self.cas, digest=cache_id).descend('logs')
        return vdir

    ################################################
    #               Local Private Methods          #
    ################################################

    # _message()
    #
    # Local message propagator
    #
    def _message(self, message_type, message, **kwargs):
        args = dict(kwargs)
        self.context.message(
            Message(None, message_type, message, **args))

    # _set_remotes():
    #
    # Set the list of remote caches. If project is None, the global list of
    # remote caches will be set, which is used by all projects. If a project is
    # specified, the per-project list of remote caches will be set.
    #
    # Args:
    #     remote_specs (list): List of ArtifactCacheSpec instances, in priority order.
    #     project (Project): The Project instance for project-specific remotes
    def _set_remotes(self, remote_specs, *, project=None):
        if project is None:
            # global remotes
            self.global_remote_specs = remote_specs
        else:
            self.project_remote_specs[project] = remote_specs

    # _initialize_remotes()
    #
    # An internal wrapper which calls the abstract method and
    # reports takes care of messaging
    #
    def _initialize_remotes(self):
        def remote_failed(url, error):
            self._message(MessageType.WARN, "Failed to initialize remote {}: {}".format(url, error))

        with self.context.timed_activity("Initializing remote caches", silent_nested=True):
            self.initialize_remotes(on_failure=remote_failed)


# _configured_remote_artifact_cache_specs():
#
# Return the list of configured artifact remotes for a given project, in priority
# order. This takes into account the user and project configuration.
#
# Args:
#     context (Context): The BuildStream context
#     project (Project): The BuildStream project
#
# Returns:
#   A list of ArtifactCacheSpec instances describing the remote artifact caches.
#
def _configured_remote_artifact_cache_specs(context, project):
    return list(utils._deduplicate(
        project.artifact_cache_specs + context.artifact_cache_specs))
