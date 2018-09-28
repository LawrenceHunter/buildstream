#
#  Copyright 2018 Bloomberg Finance LP
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library. If not, see <http://www.gnu.org/licenses/>.
#
#  Authors:
#        Jonathan Maw <jonathan.maw@codethink.co.uk>

import os
import pickle
import hashlib
import io

import sys

from contextlib import contextmanager
from collections import namedtuple

from ._cachekey import generate_key
from ._context import Context
from . import utils, _yaml


YAML_CACHE_FILENAME = "yaml_cache.pickle"


# YamlCache()
#
# A cache that wraps around the loading of yaml in projects.
#
# The recommended way to use a YamlCache is:
#   with YamlCache.open(context) as yamlcache:
#     # Load all the yaml
#     ...
#
# Args:
#    context (Context): The invocation Context
#
class YamlCache():

    def __init__(self, context):
        self._project_caches = {}
        self._context = context

    # Writes the yaml cache to the specified path.
    def write(self):
        path = self._get_cache_file(self._context)
        parent_dir = os.path.dirname(path)
        os.makedirs(parent_dir, exist_ok=True)
        with open(path, "wb") as f:
            BstPickler(f).dump(self)

    # Gets a parsed file from the cache.
    #
    # Args:
    #    project (Project): The project this file is in.
    #    filepath (str): The path to the file.
    #    key (str): The key to the file within the cache. Typically, this is the
    #               value of `calculate_key()` with the file's unparsed contents
    #               and any relevant metadata passed in.
    #
    # Returns:
    #    (decorated dict): The parsed yaml from the cache, or None if the file isn't in the cache.
    def get(self, project, filepath, key):
        cache_path = self._get_filepath(project, filepath)
        project_name = project.name if project else ""
        try:
            project_cache = self._project_caches[project_name]
            try:
                cachedyaml = project_cache.elements[cache_path]
                if cachedyaml._key == key:
                    # We've unpickled the YamlCache, but not the specific file
                    if cachedyaml._contents is None:
                        cachedyaml._contents = BstUnpickler.loads(cachedyaml._pickled_contents, self._context)
                    return cachedyaml._contents
            except KeyError:
                pass
        except KeyError:
            pass
        return None

    # Put a parsed file into the cache.
    #
    # Args:
    #    project (Project): The project this file is in.
    #    filepath (str): The path to the file.
    #    key (str): The key to the file within the cache. Typically, this is the
    #               value of `calculate_key()` with the file's unparsed contents
    #               and any relevant metadata passed in.
    #    value (decorated dict): The data to put into the cache.
    def put(self, project, filepath, key, value):
        cache_path = self._get_filepath(project, filepath)
        project_name = project.name if project else ""
        try:
            project_cache = self._project_caches[project_name]
        except KeyError:
            project_cache = self._project_caches[project_name] = CachedProject({})

        project_cache.elements[cache_path] = CachedYaml(key, value)

    # Checks whether a file is cached
    # Args:
    #    project (Project): The project this file is in.
    #    filepath (str): The path to the file, *relative to the project's directory*.
    #
    # Returns:
    #    (bool): Whether the file is cached
    def is_cached(self, project, filepath):
        cache_path = self._get_filepath(project, filepath)
        project_name = project.name if project else ""
        try:
            project_cache = self._project_caches[project_name]
            if cache_path in project_cache.elements:
                return True
        except KeyError:
            pass
        return False

    def _get_filepath(self, project, full_path):
        if project:
            assert full_path.startswith(project.directory)
            filepath = os.path.relpath(full_path, project.directory)
        else:
            filepath = full_path
        return full_path

    # Return an instance of the YamlCache which writes to disk when it leaves scope.
    #
    # Args:
    #    context (Context): The context.
    #
    # Returns:
    #    (YamlCache): A YamlCache.
    @staticmethod
    @contextmanager
    def open(context):
        # Try to load from disk first
        cachefile = YamlCache._get_cache_file(context)
        cache = None
        if os.path.exists(cachefile):
            try:
                with open(cachefile, "rb") as f:
                    cache = BstUnpickler(f, context).load()
            except pickle.UnpicklingError as e:
                sys.stderr.write("Failed to load YamlCache, {}\n".format(e))

        if not cache:
            cache = YamlCache(context)

        yield cache

        cache.write()

    # Calculates a key for putting into the cache.
    @staticmethod
    def calculate_key(*args):
        string = pickle.dumps(args)
        return hashlib.sha1(string).hexdigest()

    # Retrieves a path to the yaml cache file.
    @staticmethod
    def _get_cache_file(context):
        try:
            toplevel_project = context.get_toplevel_project()
            top_dir = toplevel_project.directory
        except IndexError:
            # Context has no projects, fall back to current directory
            top_dir = os.getcwd()
        return os.path.join(top_dir, ".bst", YAML_CACHE_FILENAME)


CachedProject = namedtuple('CachedProject', ['elements'])


class CachedYaml():
    def __init__(self, key, contents):
        self._key = key
        self.set_contents(contents)

    # Sets the contents of the CachedYaml.
    #
    # Args:
    #    contents (provenanced dict): The contents to put in the cache.
    #
    def set_contents(self, contents):
        self._contents = contents
        self._pickled_contents = BstPickler.dumps(contents)

    # Pickling helper method, prevents 'contents' from being serialised
    def __getstate__(self):
        data = self.__dict__.copy()
        data['_contents'] = None
        return data


# In _yaml.load, we have a ProvenanceFile that stores the project the file
# came from. Projects can't be pickled, but it's always going to be the same
# project between invocations (unless the entire project is moved but the
# file stayed in the same place)
class BstPickler(pickle.Pickler):
    def persistent_id(self, obj):
        if isinstance(obj, _yaml.ProvenanceFile):
            if obj.project:
                # ProvenanceFile's project object cannot be stored as it is.
                project_tag = obj.project.name
                # ProvenanceFile's filename must be stored relative to the
                # project, as the project dir may move.
                name = os.path.relpath(obj.name, obj.project.directory)
            else:
                project_tag = None
                name = obj.name
            return ("ProvenanceFile", name, obj.shortname, project_tag)
        elif isinstance(obj, Context):
            return ("Context",)
        else:
            return None

    @staticmethod
    def dumps(obj):
        stream = io.BytesIO()
        BstPickler(stream).dump(obj)
        stream.seek(0)
        return stream.read()


class BstUnpickler(pickle.Unpickler):
    def __init__(self, file, context):
        super().__init__(file)
        self._context = context

    def persistent_load(self, pid):
        if pid[0] == "ProvenanceFile":
            _, tagged_name, shortname, project_tag = pid

            if project_tag is not None:
                for p in self._context.get_projects():
                    if project_tag == p.name:
                        project = p
                        break

                name = os.path.join(project.directory, tagged_name)

                if not project:
                    projects = [p.name for p in self._context.get_projects()]
                    raise pickle.UnpicklingError("No project with name {} found in {}"
                                                 .format(key_id, projects))
            else:
                project = None
                name = tagged_name

            return _yaml.ProvenanceFile(name, shortname, project)
        elif pid[0] == "Context":
            return self._context
        else:
            raise pickle.UnpicklingError("Unsupported persistent object, {}".format(pid))

    @staticmethod
    def loads(text, context):
        stream = io.BytesIO()
        stream.write(bytes(text))
        stream.seek(0)
        return BstUnpickler(stream, context).load()
