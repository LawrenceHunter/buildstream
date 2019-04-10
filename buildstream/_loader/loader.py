#
#  Copyright (C) 2018 Codethink Limited
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
#        Tristan Van Berkom <tristan.vanberkom@codethink.co.uk>

import os
from functools import cmp_to_key
from collections.abc import Mapping

from .._exceptions import LoadError, LoadErrorReason
from .. import Consistency
from .. import _yaml
from ..element import Element
from .._profile import Topics, PROFILER
from .._includes import Includes

from .types import Symbol
from .loadelement import LoadElement, _extract_depends_from_node
from .metaelement import MetaElement
from .metasource import MetaSource
from ..types import CoreWarnings
from .._message import Message, MessageType


# Loader():
#
# The Loader class does the heavy lifting of parsing target
# bst files and ultimately transforming them into a list of MetaElements
# with their own MetaSources, ready for instantiation by the core.
#
# Args:
#    context (Context): The Context object
#    project (Project): The toplevel Project object
#    parent (Loader): A parent Loader object, in the case this is a junctioned Loader
#
class Loader():

    def __init__(self, context, project, *, parent=None):

        # Ensure we have an absolute path for the base directory
        basedir = project.element_path
        if not os.path.isabs(basedir):
            basedir = os.path.abspath(basedir)

        #
        # Public members
        #
        self.project = project   # The associated Project

        #
        # Private members
        #
        self._context = context
        self._options = project.options      # Project options (OptionPool)
        self._basedir = basedir              # Base project directory
        self._first_pass_options = project.first_pass_config.options  # Project options (OptionPool)
        self._parent = parent                # The parent loader

        self._meta_elements = {}  # Dict of resolved meta elements by name
        self._elements = {}       # Dict of elements
        self._loaders = {}        # Dict of junction loaders

        self._includes = Includes(self, copy_tree=True)

    # load():
    #
    # Loads the project based on the parameters given to the constructor
    #
    # Args:
    #    rewritable (bool): Whether the loaded files should be rewritable
    #                       this is a bit more expensive due to deep copies
    #    ticker (callable): An optional function for tracking load progress
    #    targets (list of str): Target, element-path relative bst filenames in the project
    #    fetch_subprojects (bool): Whether to fetch subprojects while loading
    #
    # Raises: LoadError
    #
    # Returns: The toplevel LoadElement
    def load(self, targets, rewritable=False, ticker=None, fetch_subprojects=False):

        for filename in targets:
            if os.path.isabs(filename):
                # XXX Should this just be an assertion ?
                # Expect that the caller gives us the right thing at least ?
                raise LoadError(LoadErrorReason.INVALID_DATA,
                                "Target '{}' was not specified as a relative "
                                "path to the base project directory: {}"
                                .format(filename, self._basedir))

        self._warn_invalid_elements(targets)

        # First pass, recursively load files and populate our table of LoadElements
        #
        target_elements = []

        for target in targets:
            with PROFILER.profile(Topics.LOAD_PROJECT, target):
                _junction, name, loader = self._parse_name(target, rewritable, ticker,
                                                           fetch_subprojects=fetch_subprojects)
                element = loader._load_file(name, rewritable, ticker, fetch_subprojects)
                target_elements.append(element)

        #
        # Now that we've resolve the dependencies, scan them for circular dependencies
        #

        # Set up a dummy element that depends on all top-level targets
        # to resolve potential circular dependencies between them
        dummy_target = LoadElement(_yaml.new_empty_node(), "", self)
        dummy_target.dependencies.extend(
            LoadElement.Dependency(element, Symbol.RUNTIME)
            for element in target_elements
        )

        with PROFILER.profile(Topics.CIRCULAR_CHECK, "_".join(targets)):
            self._check_circular_deps(dummy_target)

        ret = []
        #
        # Sort direct dependencies of elements by their dependency ordering
        #
        for element in target_elements:
            loader = element._loader
            with PROFILER.profile(Topics.SORT_DEPENDENCIES, element.name):
                loader._sort_dependencies(element)

            # Finally, wrap what we have into LoadElements and return the target
            #
            ret.append(loader._collect_element(element))

        self._clean_caches()

        return ret

    # clean_caches()
    #
    # Clean internal loader caches, recursively
    #
    # When loading the elements, the loaders use caches in order to not load the
    # same element twice. These are kept after loading and prevent garbage
    # collection. Cleaning them explicitely is required.
    #
    def _clean_caches(self):
        for loader in self._loaders.values():
            # value may be None with nested junctions without overrides
            if loader is not None:
                loader._clean_caches()

        self._meta_elements = {}
        self._elements = {}

    ###########################################
    #            Private Methods              #
    ###########################################

    # _load_file():
    #
    # Recursively load bst files
    #
    # Args:
    #    filename (str): The element-path relative bst file
    #    rewritable (bool): Whether we should load in round trippable mode
    #    ticker (callable): A callback to report loaded filenames to the frontend
    #    fetch_subprojects (bool): Whether to fetch subprojects while loading
    #    provenance (Provenance): The location from where the file was referred to, or None
    #
    # Returns:
    #    (LoadElement): A loaded LoadElement
    #
    def _load_file(self, filename, rewritable, ticker, fetch_subprojects, provenance=None):

        # Silently ignore already loaded files
        if filename in self._elements:
            return self._elements[filename]

        # Call the ticker
        if ticker:
            ticker(filename)

        # Load the data and process any conditional statements therein
        fullpath = os.path.join(self._basedir, filename)
        try:
            node = _yaml.load(fullpath, shortname=filename, copy_tree=rewritable,
                              project=self.project)
        except LoadError as e:
            if e.reason == LoadErrorReason.MISSING_FILE:

                if self.project.junction:
                    message = "Could not find element '{}' in project referred to by junction element '{}'" \
                              .format(filename, self.project.junction.name)
                else:
                    message = "Could not find element '{}' in elements directory '{}'".format(filename, self._basedir)

                if provenance:
                    message = "{}: {}".format(provenance, message)

                # If we can't find the file, try to suggest plausible
                # alternatives by stripping the element-path from the given
                # filename, and verifying that it exists.
                detail = None
                elements_dir = os.path.relpath(self._basedir, self.project.directory)
                element_relpath = os.path.relpath(filename, elements_dir)
                if filename.startswith(elements_dir) and os.path.exists(os.path.join(self._basedir, element_relpath)):
                    detail = "Did you mean '{}'?".format(element_relpath)

                raise LoadError(LoadErrorReason.MISSING_FILE,
                                message, detail=detail) from e

            elif e.reason == LoadErrorReason.LOADING_DIRECTORY:
                # If a <directory>.bst file exists in the element path,
                # let's suggest this as a plausible alternative.
                message = str(e)
                if provenance:
                    message = "{}: {}".format(provenance, message)
                detail = None
                if os.path.exists(os.path.join(self._basedir, filename + '.bst')):
                    element_name = filename + '.bst'
                    detail = "Did you mean '{}'?\n".format(element_name)
                raise LoadError(LoadErrorReason.LOADING_DIRECTORY,
                                message, detail=detail) from e
            else:
                raise
        kind = _yaml.node_get(node, str, Symbol.KIND)
        if kind == "junction":
            self._first_pass_options.process_node(node)
        else:
            self.project.ensure_fully_loaded()

            self._includes.process(node)

            self._options.process_node(node)

        element = LoadElement(node, filename, self)

        self._elements[filename] = element

        dependencies = _extract_depends_from_node(node)

        # Load all dependency files for the new LoadElement
        for dep in dependencies:
            if dep.junction:
                self._load_file(dep.junction, rewritable, ticker, fetch_subprojects, dep.provenance)
                loader = self._get_loader(dep.junction, rewritable=rewritable, ticker=ticker,
                                          fetch_subprojects=fetch_subprojects, provenance=dep.provenance)
            else:
                loader = self

            dep_element = loader._load_file(dep.name, rewritable, ticker,
                                            fetch_subprojects, dep.provenance)

            if _yaml.node_get(dep_element.node, str, Symbol.KIND) == 'junction':
                raise LoadError(LoadErrorReason.INVALID_DATA,
                                "{}: Cannot depend on junction"
                                .format(dep.provenance))

            element.dependencies.append(LoadElement.Dependency(dep_element, dep.dep_type))

        deps_names = [dep.name for dep in dependencies]
        self._warn_invalid_elements(deps_names)

        return element

    # _check_circular_deps():
    #
    # Detect circular dependencies on LoadElements with
    # dependencies already resolved.
    #
    # Args:
    #    element (str): The element to check
    #
    # Raises:
    #    (LoadError): In case there was a circular dependency error
    #
    def _check_circular_deps(self, element, check_elements=None, validated=None, sequence=None):

        if check_elements is None:
            check_elements = set()
        if validated is None:
            validated = set()
        if sequence is None:
            sequence = []

        # Skip already validated branches
        if element in validated:
            return

        if element in check_elements:
            # Create `chain`, the loop of element dependencies from this
            # element back to itself, by trimming everything before this
            # element from the sequence under consideration.
            chain = sequence[sequence.index(element.full_name):]
            chain.append(element.full_name)
            raise LoadError(LoadErrorReason.CIRCULAR_DEPENDENCY,
                            ("Circular dependency detected at element: {}\n" +
                             "Dependency chain: {}")
                            .format(element.full_name, " -> ".join(chain)))

        # Push / Check each dependency / Pop
        check_elements.add(element)
        sequence.append(element.full_name)
        for dep in element.dependencies:
            dep.element._loader._check_circular_deps(dep.element, check_elements, validated, sequence)
        check_elements.remove(element)
        sequence.pop()

        # Eliminate duplicate paths
        validated.add(element)

    # _sort_dependencies():
    #
    # Sort dependencies of each element by their dependencies,
    # so that direct dependencies which depend on other direct
    # dependencies (directly or indirectly) appear later in the
    # list.
    #
    # This avoids the need for performing multiple topological
    # sorts throughout the build process.
    #
    # Args:
    #    element (LoadElement): The element to sort
    #
    def _sort_dependencies(self, element, visited=None):
        if visited is None:
            visited = set()

        if element in visited:
            return

        for dep in element.dependencies:
            dep.element._loader._sort_dependencies(dep.element, visited=visited)

        def dependency_cmp(dep_a, dep_b):
            element_a = dep_a.element
            element_b = dep_b.element

            # Sort on inter element dependency first
            if element_a.depends(element_b):
                return 1
            elif element_b.depends(element_a):
                return -1

            # If there are no inter element dependencies, place
            # runtime only dependencies last
            if dep_a.dep_type != dep_b.dep_type:
                if dep_a.dep_type == Symbol.RUNTIME:
                    return 1
                elif dep_b.dep_type == Symbol.RUNTIME:
                    return -1

            # All things being equal, string comparison.
            if element_a.name > element_b.name:
                return 1
            elif element_a.name < element_b.name:
                return -1

            # Sort local elements before junction elements
            # and use string comparison between junction elements
            if element_a.junction and element_b.junction:
                if element_a.junction > element_b.junction:
                    return 1
                elif element_a.junction < element_b.junction:
                    return -1
            elif element_a.junction:
                return -1
            elif element_b.junction:
                return 1

            # This wont ever happen
            return 0

        # Now dependency sort, we ensure that if any direct dependency
        # directly or indirectly depends on another direct dependency,
        # it is found later in the list.
        element.dependencies.sort(key=cmp_to_key(dependency_cmp))

        visited.add(element)

    # _collect_element()
    #
    # Collect the toplevel elements we have
    #
    # Args:
    #    element (LoadElement): The element for which to load a MetaElement
    #
    # Returns:
    #    (MetaElement): A recursively loaded MetaElement
    #
    def _collect_element(self, element):
        # Return the already built one, if we already built it
        meta_element = self._meta_elements.get(element.name)
        if meta_element:
            return meta_element

        node = element.node
        elt_provenance = _yaml.node_get_provenance(node)
        meta_sources = []

        sources = _yaml.node_get(node, list, Symbol.SOURCES, default_value=[])
        element_kind = _yaml.node_get(node, str, Symbol.KIND)

        # Safe loop calling into _yaml.node_get() for each element ensures
        # we have good error reporting
        for i in range(len(sources)):
            source = _yaml.node_get(node, Mapping, Symbol.SOURCES, indices=[i])
            kind = _yaml.node_get(source, str, Symbol.KIND)
            _yaml.node_del(source, Symbol.KIND)

            # Directory is optional
            directory = _yaml.node_get(source, str, Symbol.DIRECTORY, default_value=None)
            if directory:
                _yaml.node_del(source, Symbol.DIRECTORY)

            index = sources.index(source)
            meta_source = MetaSource(element.name, index, element_kind, kind, source, directory)
            meta_sources.append(meta_source)

        meta_element = MetaElement(self.project, element.name, element_kind,
                                   elt_provenance, meta_sources,
                                   _yaml.node_get(node, Mapping, Symbol.CONFIG, default_value={}),
                                   _yaml.node_get(node, Mapping, Symbol.VARIABLES, default_value={}),
                                   _yaml.node_get(node, Mapping, Symbol.ENVIRONMENT, default_value={}),
                                   _yaml.node_get(node, list, Symbol.ENV_NOCACHE, default_value=[]),
                                   _yaml.node_get(node, Mapping, Symbol.PUBLIC, default_value={}),
                                   _yaml.node_get(node, Mapping, Symbol.SANDBOX, default_value={}),
                                   element_kind == 'junction')

        # Cache it now, make sure it's already there before recursing
        self._meta_elements[element.name] = meta_element

        # Descend
        for dep in element.dependencies:
            loader = dep.element._loader
            meta_dep = loader._collect_element(dep.element)
            if dep.dep_type != 'runtime':
                meta_element.build_dependencies.append(meta_dep)
            if dep.dep_type != 'build':
                meta_element.dependencies.append(meta_dep)

        return meta_element

    # _get_loader():
    #
    # Return loader for specified junction
    #
    # Args:
    #    filename (str): Junction name
    #    fetch_subprojects (bool): Whether to fetch subprojects while loading
    #
    # Raises: LoadError
    #
    # Returns: A Loader or None if specified junction does not exist
    def _get_loader(self, filename, *, rewritable=False, ticker=None, level=0,
                    fetch_subprojects=False, provenance=None):

        provenance_str = ""
        if provenance is not None:
            provenance_str = "{}: ".format(provenance)

        # return previously determined result
        if filename in self._loaders:
            loader = self._loaders[filename]

            if loader is None:
                # do not allow junctions with the same name in different
                # subprojects
                raise LoadError(LoadErrorReason.CONFLICTING_JUNCTION,
                                "{}Conflicting junction {} in subprojects, define junction in {}"
                                .format(provenance_str, filename, self.project.name))

            return loader

        if self._parent:
            # junctions in the parent take precedence over junctions defined
            # in subprojects
            loader = self._parent._get_loader(filename, rewritable=rewritable, ticker=ticker,
                                              level=level + 1, fetch_subprojects=fetch_subprojects,
                                              provenance=provenance)
            if loader:
                self._loaders[filename] = loader
                return loader

        try:
            self._load_file(filename, rewritable, ticker, fetch_subprojects)
        except LoadError as e:
            if e.reason != LoadErrorReason.MISSING_FILE:
                # other load error
                raise

            if level == 0:
                # junction element not found in this or ancestor projects
                raise
            else:
                # mark junction as not available to allow detection of
                # conflicting junctions in subprojects
                self._loaders[filename] = None
                return None

        # meta junction element
        meta_element = self._collect_element(self._elements[filename])
        if meta_element.kind != 'junction':
            raise LoadError(LoadErrorReason.INVALID_DATA,
                            "{}{}: Expected junction but element kind is {}".format(
                                provenance_str, filename, meta_element.kind))

        element = Element._new_from_meta(meta_element)
        element._preflight()

        sources = list(element.sources())
        if not element._source_cached():
            for idx, source in enumerate(sources):
                # Handle the case where a subproject needs to be fetched
                #
                if source.get_consistency() == Consistency.RESOLVED:
                    if fetch_subprojects:
                        if ticker:
                            ticker(filename, 'Fetching subproject from {} source'.format(source.get_kind()))
                        source._fetch(sources[0:idx])
                    else:
                        detail = "Try fetching the project with `bst source fetch {}`".format(filename)
                        raise LoadError(LoadErrorReason.SUBPROJECT_FETCH_NEEDED,
                                        "{}Subproject fetch needed for junction: {}".format(provenance_str, filename),
                                        detail=detail)

                # Handle the case where a subproject has no ref
                #
                elif source.get_consistency() == Consistency.INCONSISTENT:
                    detail = "Try tracking the junction element with `bst source track {}`".format(filename)
                    raise LoadError(LoadErrorReason.SUBPROJECT_INCONSISTENT,
                                    "{}Subproject has no ref for junction: {}".format(provenance_str, filename),
                                    detail=detail)

        workspace = element._get_workspace()
        if workspace:
            # If a workspace is open, load it from there instead
            basedir = workspace.get_absolute_path()
        elif len(sources) == 1 and sources[0]._get_local_path():
            # Optimization for junctions with a single local source
            basedir = sources[0]._get_local_path()
        else:
            # Stage sources
            # TODO: New object to update cache keys here
            element._update_state()
            basedir = os.path.join(self.project.directory, ".bst", "staged-junctions",
                                   filename, element._get_cache_key())
            if not os.path.exists(basedir):
                os.makedirs(basedir, exist_ok=True)
                element._stage_sources_at(basedir, mount_workspaces=False)

        # Load the project
        project_dir = os.path.join(basedir, element.path)
        try:
            from .._project import Project  # pylint: disable=cyclic-import
            project = Project(project_dir, self._context, junction=element,
                              parent_loader=self, search_for_project=False)
        except LoadError as e:
            if e.reason == LoadErrorReason.MISSING_PROJECT_CONF:
                message = (
                    provenance_str + "Could not find the project.conf file in the project "
                    "referred to by junction element '{}'.".format(element.name)
                )
                if element.path:
                    message += " Was expecting it at path '{}' in the junction's source.".format(element.path)
                raise LoadError(reason=LoadErrorReason.INVALID_JUNCTION,
                                message=message) from e
            else:
                raise

        loader = project.loader
        self._loaders[filename] = loader

        return loader

    # _parse_name():
    #
    # Get junction and base name of element along with loader for the sub-project
    #
    # Args:
    #   name (str): Name of target
    #   rewritable (bool): Whether the loaded files should be rewritable
    #                      this is a bit more expensive due to deep copies
    #   ticker (callable): An optional function for tracking load progress
    #   fetch_subprojects (bool): Whether to fetch subprojects while loading
    #
    # Returns:
    #   (tuple): - (str): name of the junction element
    #            - (str): name of the element
    #            - (Loader): loader for sub-project
    #
    def _parse_name(self, name, rewritable, ticker, fetch_subprojects=False):
        # We allow to split only once since deep junctions names are forbidden.
        # Users who want to refer to elements in sub-sub-projects are required
        # to create junctions on the top level project.
        junction_path = name.rsplit(':', 1)
        if len(junction_path) == 1:
            return None, junction_path[-1], self
        else:
            self._load_file(junction_path[-2], rewritable, ticker, fetch_subprojects)
            loader = self._get_loader(junction_path[-2], rewritable=rewritable, ticker=ticker,
                                      fetch_subprojects=fetch_subprojects)
            return junction_path[-2], junction_path[-1], loader

    # Print a warning message, checks warning_token against project configuration
    #
    # Args:
    #     brief (str): The brief message
    #     warning_token (str): An optional configurable warning assosciated with this warning,
    #                          this will cause PluginError to be raised if this warning is configured as fatal.
    #                          (*Since 1.4*)
    #
    # Raises:
    #     (:class:`.LoadError`): When warning_token is considered fatal by the project configuration
    #
    def _warn(self, brief, *, warning_token=None):
        if warning_token:
            if self.project._warning_is_fatal(warning_token):
                raise LoadError(warning_token, brief)

        message = Message(None, MessageType.WARN, brief)
        self._context.message(message)

    # Print warning messages if any of the specified elements have invalid names.
    #
    # Valid filenames should end with ".bst" extension.
    #
    # Args:
    #    elements (list): List of element names
    #
    # Raises:
    #     (:class:`.LoadError`): When warning_token is considered fatal by the project configuration
    #
    def _warn_invalid_elements(self, elements):

        # invalid_elements
        #
        # A dict that maps warning types to the matching elements.
        invalid_elements = {
            CoreWarnings.BAD_ELEMENT_SUFFIX: [],
            CoreWarnings.BAD_CHARACTERS_IN_NAME: [],
        }

        for filename in elements:
            if not filename.endswith(".bst"):
                invalid_elements[CoreWarnings.BAD_ELEMENT_SUFFIX].append(filename)
            if not self._valid_chars_name(filename):
                invalid_elements[CoreWarnings.BAD_CHARACTERS_IN_NAME].append(filename)

        if invalid_elements[CoreWarnings.BAD_ELEMENT_SUFFIX]:
            self._warn("Target elements '{}' do not have expected file extension `.bst` "
                       "Improperly named elements will not be discoverable by commands"
                       .format(invalid_elements[CoreWarnings.BAD_ELEMENT_SUFFIX]),
                       warning_token=CoreWarnings.BAD_ELEMENT_SUFFIX)
        if invalid_elements[CoreWarnings.BAD_CHARACTERS_IN_NAME]:
            self._warn("Target elements '{}' have invalid characerts in their name."
                       .format(invalid_elements[CoreWarnings.BAD_CHARACTERS_IN_NAME]),
                       warning_token=CoreWarnings.BAD_CHARACTERS_IN_NAME)

    # Check if given filename containers valid characters.
    #
    # Args:
    #    name (str): Name of the file
    #
    # Returns:
    #    (bool): True if all characters are valid, False otherwise.
    #
    def _valid_chars_name(self, name):
        for char in name:
            char_val = ord(char)

            # 0-31 are control chars, 127 is DEL, and >127 means non-ASCII
            if char_val <= 31 or char_val >= 127:
                return False

            # Disallow characters that are invalid on Windows. The list can be
            # found at https://docs.microsoft.com/en-us/windows/desktop/FileIO/naming-a-file
            #
            # Note that although : (colon) is not allowed, we do not raise
            # warnings because of that, since we use it as a separator for
            # junctioned elements.
            #
            # We also do not raise warnings on slashes since they are used as
            # path separators.
            if char in r'<>"|?*':
                return False

        return True
