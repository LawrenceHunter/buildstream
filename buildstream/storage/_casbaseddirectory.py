#
#  Copyright (C) 2018 Bloomberg LP
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
#        Jim MacArthur <jim.macarthur@codethink.co.uk>

"""
CasBasedDirectory
=========

Implementation of the Directory class which backs onto a Merkle-tree based content
addressable storage system.

See also: :ref:`sandboxing`.
"""

from collections import OrderedDict

import os
import tempfile
import stat

from .._protos.build.bazel.remote.execution.v2 import remote_execution_pb2
from .._exceptions import BstError
from .directory import Directory, VirtualDirectoryError
from ._filebaseddirectory import FileBasedDirectory
from ..utils import FileListResult, safe_copy, list_relative_paths
from ..utils import FileListResult, safe_copy, list_relative_paths, _relative_symlink_target
from .._artifactcache.cascache import CASCache

import copy # Temporary
import operator

class IndexEntry():
    """ Used in our index of names to objects to store the 'modified' flag
    for directory entries. Because we need both the remote_execution_pb2 object
    and our own Directory object for directory entries, we store both. For files
    and symlinks, only pb_object is used. """
    def __init__(self, pb_object, buildstream_object=None, modified=False):
        self.pb_object = pb_object  # Short for 'protocol buffer object')
        self.buildstream_object = buildstream_object
        self.modified = modified


# CasBasedDirectory intentionally doesn't call its superclass constuctor,
# which is meant to be unimplemented.
# pylint: disable=super-init-not-called

class CasBasedDirectory(Directory):
    """
    CAS-based directories can have two names; one is a 'common name' which has no effect
    on functionality, and the 'filename'. If a CasBasedDirectory has a parent, then 'filename'
    must be the name of an entry in the parent directory's index which points to this object.
    This is used to inform a parent directory that it must update the given hash for this
    object when this object changes.

    Typically a top-level CasBasedDirectory will have a common_name and no filename, and
    subdirectories wil have a filename and no common_name. common_name can used to identify
    CasBasedDirectory objects in a log file, since they have no unique position in a file
    system.
    """

    # Two constants which define the separators used by the remote execution API.
    _pb2_path_sep = "/"
    _pb2_absolute_path_prefix = "/"

    def __init__(self, context, ref=None, parent=None, common_name="untitled", filename=None):
        self.context = context
        self.cas_directory = os.path.join(context.artifactdir, 'cas')
        self.filename = filename
        self.common_name = common_name
        self.pb2_directory = remote_execution_pb2.Directory()
        self.cas_cache = context.artifactcache
        if ref:
            with open(self.cas_cache.objpath(ref), 'rb') as f:
                self.pb2_directory.ParseFromString(f.read())
                print("Opening ref {} and parsed into directory containing: {} {} {}.".format(ref.hash, [d.name for d in self.pb2_directory.directories],
                                                                                        [d.name for d in self.pb2_directory.symlinks],
                                                                                        [d.name for d in self.pb2_directory.files]))
        self.ref = ref
        self.index = OrderedDict()
        self.parent = parent
        self._directory_read = False
        self._populate_index()

    def _populate_index(self):
        if self._directory_read:
            return
        for entry in self.pb2_directory.directories:
            buildStreamDirectory = CasBasedDirectory(self.context, ref=entry.digest,
                                                     parent=self, filename=entry.name)
            self.index[entry.name] = IndexEntry(entry, buildstream_object=buildStreamDirectory)
        for entry in self.pb2_directory.files:
            self.index[entry.name] = IndexEntry(entry)
        for entry in self.pb2_directory.symlinks:
            self.index[entry.name] = IndexEntry(entry)
        self._directory_read = True

    def _recalculate_recursing_up(self, caller=None):
        """Recalcuate the hash for this directory and store the results in
        the cache.  If this directory has a parent, tell it to
        recalculate (since changing this directory changes an entry in
        the parent).

        """
        self.ref = self.cas_cache.add_object(buffer=self.pb2_directory.SerializeToString())
        if caller:
            old_dir = self._find_pb2_entry(caller.filename)
            self.cas_cache.add_object(digest=old_dir.digest, buffer=caller.pb2_directory.SerializeToString())
        if self.parent:
            self.parent._recalculate_recursing_up(self)

    def _recalculate_recursing_down(self, parent=None):
        """Recalcuate the hash for this directory and any
        subdirectories. Hashes for subdirectories should be calculated
        and stored after a significant operation (e.g. an
        import_files() call) but not after adding each file, as that
        is extremely wasteful.

        """
        for entry in self.pb2_directory.directories:
            self.index[entry.name].buildstream_object._recalculate_recursing_down(entry)

        if parent:
            self.ref = self.cas_cache.add_object(digest=parent.digest, buffer=self.pb2_directory.SerializeToString())
        else:
            self.ref = self.cas_cache.add_object(buffer=self.pb2_directory.SerializeToString())
        # We don't need to do anything more than that; files were already added ealier, and symlinks are
        # part of the directory structure.

    def _add_new_blank_directory(self, name) -> Directory:
        bst_dir = CasBasedDirectory(self.context, parent=self, filename=name)
        new_pb2_dirnode = self.pb2_directory.directories.add()
        new_pb2_dirnode.name = name
        # Calculate the hash for an empty directory
        if name in self.index:
            raise VirtualDirectoryError("Creating directory {} would overwrite an existing item in {}"
                                        .format(name, str(self)))
        new_pb2_directory = remote_execution_pb2.Directory()
        self.cas_cache.add_object(digest=new_pb2_dirnode.digest, buffer=new_pb2_directory.SerializeToString())
        self.index[name] = IndexEntry(new_pb2_dirnode, buildstream_object=bst_dir)
        return bst_dir

    def create_directory(self, name: str) -> Directory:
        """Creates a directory if it does not already exist. This does not
        cause an error if something exists; it will remove files and
        symlinks to files which have the same name in this
        directory. Symlinks to directories with the name 'name' are
        unaltered; it's assumed that the target of that symlink will
        be used.

        """
        existing_item = self._find_pb2_entry(name)
        if isinstance(existing_item, remote_execution_pb2.FileNode):
            # Directory imported over file with same name
            self.delete_entry(name)
        elif isinstance(existing_item, remote_execution_pb2.SymlinkNode):
            # Directory imported over symlink with same source name
            if self.symlink_target_is_directory(existing_item):
                return self._resolve_symlink_or_directory(name) # That's fine; any files in the source directory should end up at the target of the symlink.
            else:
                self.delete_entry(name) # Symlinks to files get replaced
        return self.descend(name, create=True) # Creates the directory if it doesn't already exist.


    def _find_pb2_entry(self, name):
        if name in self.index:
            return self.index[name].pb_object
        return None

    def _find_self_in_parent(self):
        assert self.parent is not None
        parent = self.parent
        for (k, v) in parent.index.items():
            if v.buildstream_object == self:
                return k
        return None

    def _add_directory(self, name):
        if name in self.index:
            newdir = self.index[name].buildstream_object
            if not isinstance(newdir, CasBasedDirectory):
                # TODO: This may not be an actual error; it may actually overwrite it
                raise VirtualDirectoryError("New directory {} in {} would overwrite existing non-directory of type {}"
                                            .format(name, str(self), type(newdir)))
            dirnode = self._find_pb2_entry(name)
        else:
            newdir = CasBasedDirectory(self.context, parent=self, filename=name)
            dirnode = self.pb2_directory.directories.add()

        dirnode.name = name

        # Calculate the hash for an empty directory
        new_directory = remote_execution_pb2.Directory()
        self.cas_cache.add_object(digest=dirnode.digest, buffer=new_directory.SerializeToString())
        self.index[name] = IndexEntry(dirnode, buildstream_object=newdir)
        return newdir

    def _add_new_file(self, basename, filename):
        filenode = self.pb2_directory.files.add()
        filenode.name = filename
        self.cas_cache.add_object(digest=filenode.digest, path=os.path.join(basename, filename))
        is_executable = os.access(os.path.join(basename, filename), os.X_OK)
        filenode.is_executable = is_executable
        self.index[filename] = IndexEntry(filenode, modified=(filename in self.index))

    def _add_new_link(self, basename, filename):
        existing_link = self._find_pb2_entry(filename)
        if existing_link:
            symlinknode = existing_link
        else:
            symlinknode = self.pb2_directory.symlinks.add()
        symlinknode.name = filename
        # A symlink node has no digest.
        symlinknode.target = os.readlink(os.path.join(basename, filename))
        self.index[filename] = IndexEntry(symlinknode, modified=(existing_link is not None))

    def _add_new_link_direct(self, name, target):
        existing_link = self._find_pb2_entry(name)
        if existing_link:
            symlinknode = existing_link
        else:
            symlinknode = self.pb2_directory.symlinks.add()
        assert(isinstance(symlinknode, remote_execution_pb2.SymlinkNode))
        symlinknode.name = name
        # A symlink node has no digest.
        symlinknode.target = target
        self.index[name] = IndexEntry(symlinknode, modified=(existing_link is not None))

        
    def delete_entry(self, name):
        for collection in [self.pb2_directory.files, self.pb2_directory.symlinks, self.pb2_directory.directories]:
            for thing in collection:
                if thing.name == name:
                    print("Removing {} from PB2".format(name))
                    collection.remove(thing)
        if name in self.index:
            print("Removing {} from index".format(name))
            del self.index[name]

    def descend(self, subdirectory_spec, create=False):
        """Descend one or more levels of directory hierarchy and return a new
        Directory object for that directory.

        Arguments:
        * subdirectory_spec (list of strings): A list of strings which are all directory
          names.
        * create (boolean): If this is true, the directories will be created if
          they don't already exist.

        Note: At the moment, creating a directory by descending does
        not update this object in the CAS cache. However, performing
        an import_files() into a subdirectory of any depth obtained by
        descending from this object *will* cause this directory to be
        updated and stored.

        """

        # It's very common to send a directory name instead of a list and this causes
        # bizarre errors, so check for it here
        if not isinstance(subdirectory_spec, list):
            subdirectory_spec = [subdirectory_spec]

        # Because of the way split works, it's common to get a list which begins with
        # an empty string. Detect these and remove them.
        while subdirectory_spec and subdirectory_spec[0] == "":
            subdirectory_spec.pop(0)

        # Descending into [] returns the same directory.
        if not subdirectory_spec:
            return self

        if subdirectory_spec[0] in self.index:
            entry = self.index[subdirectory_spec[0]].buildstream_object
            if isinstance(entry, CasBasedDirectory):
                return entry.descend(subdirectory_spec[1:], create)
            else:
                # May be a symlink
                error = "Cannot descend into {}, which is a '{}' in the directory {}"
                raise VirtualDirectoryError(error.format(subdirectory_spec[0],
                                                         type(entry).__name__,
                                                         self))
        else:
            if create:
                newdir = self._add_directory(subdirectory_spec[0])
                return newdir.descend(subdirectory_spec[1:], create)
            else:
                error = "No entry called '{}' found in {}. There are directories called {}."
                directory_list = ",".join([entry.name for entry in self.pb2_directory.directories])
                raise VirtualDirectoryError(error.format(subdirectory_spec[0], str(self),
                                                         directory_list))
        return None

    def find_root(self):
        """ Finds the root of this directory tree by following 'parent' until there is
        no parent. """
        if self.parent:
            return self.parent.find_root()
        else:
            return self

    def _resolve_symlink_or_directory(self, name):
        """Used only by _import_files_from_directory. Tries to resolve a
        directory name or symlink name. 'name' must be an entry in this
        directory. It must be a single symlink or directory name, not a path
        separated by path separators. If it's an existing directory name, it
        just returns the Directory object for that. If it's a symlink, it will
        attempt to find the target of the symlink and return that as a
        Directory object.

        If a symlink target doesn't exist, it will attempt to create it
        as a directory as long as it's within this directory tree.
        """

        if isinstance(self.index[name].buildstream_object, Directory):
            return self.index[name].buildstream_object
        # OK then, it's a symlink
        symlink = self._find_pb2_entry(name)
        absolute = symlink.target.startswith(CasBasedDirectory._pb2_absolute_path_prefix)
        if absolute:
            root = self.find_root()
        else:
            root = self
        directory = root
        components = symlink.target.split(CasBasedDirectory._pb2_path_sep)
        for c in components:
            if c == ".":
                pass
            elif c == "..":
                directory = directory.parent
            else:
                directory = directory.descend(c, create=True)
        return directory

    def _resolve_symlink(self, node):
        """Same as _resolve_symlink_or_directory but takes a SymlinkNode.
        """

        # OK then, it's a symlink
        symlink = node
        absolute = symlink.target.startswith(CasBasedDirectory._pb2_absolute_path_prefix)
        if absolute:
            root = self.find_root()
        else:
            root = self
        directory = root
        components = symlink.target.split(CasBasedDirectory._pb2_path_sep)
        for c in components:
            if c == ".":
                pass
            elif c == "..":
                directory = directory.parent
            else:
                directory = directory.descend(c, create=True)
        return directory

    
    def _resolve(self, name, absolute_symlinks_resolve=True):
        """ Resolves any name to an object. If the name points to a symlink in
        this directory, it returns the thing it points to,
        recursively. Returns a CasBasedDirectory, FileNode or
        None. Never creates a directory or otherwise alters the
        directory.

        """
        # First check if it's a normal object and return that

        if name not in self.index:
            return None
        index_entry = self.index[name]
        if isinstance(index_entry.buildstream_object, Directory):
            return index_entry.buildstream_object
        elif isinstance(index_entry.pb_object, remote_execution_pb2.FileNode):
            return index_entry.pb_object
        
        assert isinstance(index_entry.pb_object, remote_execution_pb2.SymlinkNode)
        symlink = index_entry.pb_object
        components = symlink.target.split(CasBasedDirectory._pb2_path_sep)

        absolute = symlink.target.startswith(CasBasedDirectory._pb2_absolute_path_prefix)
        if absolute:
            if absolute_symlinks_resolve:
                start_directory = self.find_root()
                # Discard the first empty element
                components.pop(0)
            else:
                print("  _resolve: Absolute symlink, which we won't resolve.")
                return None
        else:
            start_directory = self
        directory = start_directory
        print("Resolve {}: starting from {}".format(symlink.target, start_directory))
        while True:
            if not components:
                # We ran out of path elements and ended up in a directory
                return directory
            c = components.pop(0)
            if c == ".":
                pass
            elif c == "..":
                print("  resolving {}: up-dir".format(c))
                # If directory.parent *is* None, this is an attempt to access
                # '..' from the root, which is valid under POSIX; it just
                # returns the root.                
                if directory.parent is not None:
                    directory = directory.parent
            else:
                if c in directory.index:
                    f = directory._resolve(c, absolute_symlinks_resolve)
                    # Ultimately f must now be a file or directory
                    if isinstance(f, CasBasedDirectory):
                        directory = f
                        print("  resolving {}: dir".format(c))

                    else:
                        # This is a file or None (i.e. broken symlink)
                        print("  resolving {}: file/broken link".format(c))
                        if components:
                            # Oh dear. We have components left to resolve, but the one we're trying to resolve points to a file.
                            raise VirtualDirectoryError("Reached a file called {} while trying to resolve a symlink; cannot proceed".format(c))
                        else:
                            return f
                else:
                    print("  resolving {}: Broken symlink".format(c))
                    return None

        # Shouldn't get here.
        

    def _check_replacement(self, name, path_prefix, fileListResult):
        """ Checks whether 'name' exists, and if so, whether we can overwrite it.
        If we can, add the name to 'overwritten_files' and delete the existing entry.
        Returns 'True' if the import should go ahead.
        fileListResult.overwritten and fileListResult.ignore are updated depending
        on the result. """
        existing_entry = self._find_pb2_entry(name)
        relative_pathname = os.path.join(path_prefix, name)
        if existing_entry is None:
            return True
        if (isinstance(existing_entry,
                       (remote_execution_pb2.FileNode, remote_execution_pb2.SymlinkNode))):
            self.delete_entry(name)
            print("Processing overwrite of file/symlink {}: Added to overwritten list and deleted".format(name))
            fileListResult.overwritten.append(relative_pathname)
            return True
        elif isinstance(existing_entry, remote_execution_pb2.DirectoryNode):
            # If 'name' maps to a DirectoryNode, then there must be an entry in index
            # pointing to another Directory.
            if self.index[name].buildstream_object.is_empty():
                print("Processing overwrite of directory: Removing original")
                self.delete_entry(name)
                fileListResult.overwritten.append(relative_pathname)
                return True
            else:
                # We can't overwrite a non-empty directory, so we just ignore it.
                print("Processing overwrite of non-empty directory: Ignoring overwrite")
                fileListResult.ignored.append(relative_pathname)
                return False
        assert False, ("Entry '{}' is not a recognised file/link/directory and not None; it is {}"
                       .format(name, type(existing_entry)))
        return False  # In case asserts are disabled

    def _import_directory_recursively(self, directory_name, source_directory, remaining_path, path_prefix):
        """ _import_directory_recursively and _import_files_from_directory will be called alternately
        as a directory tree is descended. """
        if directory_name in self.index:
            subdir = self._resolve_symlink_or_directory(directory_name)
        else:
            subdir = self._add_directory(directory_name)
        new_path_prefix = os.path.join(path_prefix, directory_name)
        subdir_result = subdir._import_files_from_directory(os.path.join(source_directory, directory_name),
                                                            [os.path.sep.join(remaining_path)],
                                                            path_prefix=new_path_prefix)
        return subdir_result

    def _import_files_from_directory(self, source_directory, files, path_prefix=""):
        """ Imports files from a traditional directory """
        result = FileListResult()
        for entry in sorted(files):
            print("Importing {} from file system".format(entry))
            print("...Order of elements was {}".format(", ".join(self.index.keys())))

            split_path = entry.split(os.path.sep)
            # The actual file on the FS we're importing
            import_file = os.path.join(source_directory, entry)
            # The destination filename, relative to the root where the import started
            relative_pathname = os.path.join(path_prefix, entry)
            if len(split_path) > 1:
                directory_name = split_path[0]
                # Hand this off to the importer for that subdir. This will only do one file -
                # a better way would be to hand off all the files in this subdir at once.
                subdir_result = self._import_directory_recursively(directory_name, source_directory,
                                                                   split_path[1:], path_prefix)
                result.combine(subdir_result)
            elif os.path.islink(import_file):
                if self._check_replacement(entry, path_prefix, result):
                    self._add_new_link(source_directory, entry)
                    result.files_written.append(relative_pathname)
            elif os.path.isdir(import_file):
                # A plain directory which already exists isn't a problem; just ignore it.
                if entry not in self.index:
                    self._add_directory(entry)
            elif os.path.isfile(import_file):
                if self._check_replacement(entry, path_prefix, result):
                    self._add_new_file(source_directory, entry)
                    result.files_written.append(relative_pathname)
            print("...Order of elements is now {}".format(", ".join(self.index.keys())))

        return result


    def _save(self, name):
        """ Saves this directory into the content cache as a named ref. This function is not
        currently in use, but may be useful later. """
        self._recalculate_recursing_up()
        self._recalculate_recursing_down()
        (rel_refpath, refname) = os.path.split(name)
        refdir = os.path.join(self.cas_directory, 'refs', 'heads', rel_refpath)
        refname = os.path.join(refdir, refname)

        if not os.path.exists(refdir):
            os.makedirs(refdir)
        with open(refname, "wb") as f:
            f.write(self.ref.SerializeToString())

    def find_updated_files(self, modified_directory, prefix=""):
        """Find the list of written and overwritten files that would result
        from importing 'modified_directory' into this one.  This does
        not change either directory. The reason this exists is for
        direct imports of cas directories into other ones, which can
        be done by simply replacing a hash, but we still need the file
        lists.

        """
        result = FileListResult()
        for entry in modified_directory.pb2_directory.directories:
            existing_dir = self._find_pb2_entry(entry.name)
            if existing_dir:
                updates_files = existing_dir.find_updated_files(modified_directory.descend(entry.name),
                                                                os.path.join(prefix, entry.name))
                result.combine(updated_files)
            else:
                for f in source_directory.descend(entry.name).list_relative_paths():
                    result.files_written.append(os.path.join(prefix, f))
                    # None of these can overwrite anything, since the original files don't exist
        for entry in modified_directory.pb2_directory.files + modified_directory.pb2_directory.symlinks:
            if self._find_pb2_entry(entry.name):
                result.files_overwritten.apppend(os.path.join(prefix, entry.name))
            result.file_written.apppend(os.path.join(prefix, entry.name))
        return result

    def files_in_subdir(sorted_files, dirname):
        """Filters sorted_files and returns only the ones which have
           'dirname' as a prefix, with that prefix removed.

        """
        if not dirname.endswith(os.path.sep):
            dirname += os.path.sep
        return [f[len(dirname):] for f in sorted_files if f.startswith(dirname)]

    def symlink_target_is_directory(self, symlink_node):
        x = self._resolve_symlink(symlink_node)
        return isinstance(x, CasBasedDirectory)

    def _verify_unique(self):
        # Verifies that there are no duplicate names in this directory or subdirectories.
        names = []
        for entrylist in [self.pb2_directory.files, self.pb2_directory.directories, self.pb2_directory.symlinks]:
            for e in entrylist:
                if e.name in names:
                    raise VirtualDirectoryError("Duplicate entry for name {} found".format(e.name))
                names.append(e.name)
        for d in self.pb2_directory.directories:
            self.index[d.name].buildstream_object._verify_unique()
    
    def _partial_import_cas_into_cas(self, source_directory, files, path_prefix="", file_list_required=True):
        """ Import only the files and symlinks listed in 'files' from source_directory to this one.
        Args:
           source_directory (:class:`.CasBasedDirectory`): The directory to import from
           files ([str]): List of pathnames to import.
           path_prefix (str): Prefix used to add entries to the file list result.
           file_list_required: Whether to update the file list while processing.
        """
        print("Beginning partial import of {} into {}. Files are: >{}<".format(source_directory, self, ", ".join(files)))
        result = FileListResult()
        processed_directories = set()
        for f in files:
            if f == ".": continue
            fullname = os.path.join(path_prefix, f)
            components = f.split(os.path.sep)
            if len(components)>1:
                # We are importing a thing which is in a subdirectory. We may have already seen this dirname
                # for a previous file.
                dirname = components[0]
                if dirname not in processed_directories:
                    # Now strip off the first directory name and import files recursively.
                    subcomponents = CasBasedDirectory.files_in_subdir(files, dirname)
                    self.create_directory(dirname)
                    print("Creating destination in {}: {}".format(self, dirname))
                    dest_subdir = self._resolve_symlink_or_directory(dirname)
                    src_subdir = source_directory.descend(dirname)
                    import_result = dest_subdir._partial_import_cas_into_cas(src_subdir, subcomponents,
                                                                             path_prefix=fullname, file_list_required=file_list_required)
                    result.combine(import_result)
                processed_directories.add(dirname)
            elif isinstance(source_directory.index[f].buildstream_object, CasBasedDirectory):
                # The thing in the input file list is a directory on its own. In which case, replace any existing file, or symlink to file
                # with the new, blank directory - if it's neither of those things, or doesn't exist, then just create the dir.
                self.create_directory(f)
            else:
                # We're importing a file or symlink - replace anything with the same name.
                print("Import of file/symlink {} into this directory. Removing anything existing...".format(f))
                print("   ... ordering of nodes in this dir was: {}".format(self.index.keys()))
                print("   ... symlinks were {}".format([x.name for x in self.pb2_directory.symlinks]))
                importable = self._check_replacement(f, path_prefix, result)
                if importable:
                    print("   ... after replacement of '{}', symlinks are now {}".format(f, [x.name for x in self.pb2_directory.symlinks]))
                    item = source_directory.index[f].pb_object
                    if isinstance(item, remote_execution_pb2.FileNode):
                        print("   ... importing file")
                        filenode = self.pb2_directory.files.add(digest=item.digest, name=f,
                                                                is_executable=item.is_executable)
                        self.index[f] = IndexEntry(filenode, modified=(fullname in result.overwritten))
                    else:
                        print("   ... importing symlink")
                        assert(isinstance(item, remote_execution_pb2.SymlinkNode))
                        self._add_new_link_direct(name=f, target=item.target)
                        print("   ... symlinks are now {}".format([x.name for x in self.pb2_directory.symlinks]))
                    print("   ... ordering of nodes in this dir is now: {}".format(self.index.keys()))
        return result

    def transfer_node_contents(destination, source):
        """Transfers all fields from the source PB2 node into the
        destination. Destination and source must be of the same type and must
        be a FileNode, SymlinkNode or DirectoryNode.
        """
        assert(type(destination) == type(source))
        destination.name = source.name
        if isinstance(destination, remote_execution_pb2.FileNode):
            destination.digest.hash = source.digest.hash
            destination.digest.size_bytes = source.digest.size_bytes
            destination.is_executable = source.is_executable
        elif isinstance(destination, remote_execution_pb2.SymlinkNode):
            destination.target = source.target
        elif isinstance(destination, remote_execution_pb2.DirectoryNode):
            destination.digest.hash = source.digest.hash
            destination.digest.size_bytes = source.digest.size_bytes
        else:
            raise VirtualDirectoryError("Incompatible type '{}' used as destination for transfer_node_contents"
                                        .format(destination.type))

    def _add_directory_from_node(self, source_node, source_casdir, can_hardlink=False):
        # Duplicate the given node and add it to our index with a CasBasedDirectory object.
        # No existing entry with the source node's name can exist.
        # source_casdir is only needed if can_hardlink is True.
        assert(self._find_pb2_entry(source_node.name) is None)

        if can_hardlink:
            new_dir_node = self.pb2_directory.directories.add()
            CasBasedDirectory.transfer_node_contents(new_dir_node, source_node)
            self.index[source_node.name] = IndexEntry(source_node, buildstream_object=source_casdir, modified=True)
        else:
            new_dir_node = self.pb2_directory.directories.add()
            CasBasedDirectory.transfer_node_contents(new_dir_node, source_node)
            buildStreamDirectory = CasBasedDirectory(self.context, ref=source_node.digest,
                                                     parent=self, filename=source_node.name)
            self.index[source_node.name] = IndexEntry(source_node, buildstream_object=buildStreamDirectory, modified=True)

    def _import_cas_into_cas(self, source_directory, files=None):
        """ A full import is significantly quicker than a partial import, because we can just
        replace one directory with another's hash, without doing any recursion.
        """
        if files is None:
            #return self._full_import_cas_into_cas(source_directory, can_hardlink=True)
            files = list(source_directory.list_relative_paths())
            print("Extracted all files from source directory '{}': {}".format(source_directory, files))
        return self._partial_import_cas_into_cas(source_directory, list(files))

    def showdiff(self, other):
        print("Diffing {} and {}:".format(self, other))

        def compare_list(l1, l2):
            item2 = None
            index = 0
            print("Comparing lists: {} vs {}".format([d.name for d in l1], [d.name for d in l2]))
            for item1 in l1:
                if index>=len(l2):
                    print("l2 is short: no item to correspond to '{}' in l1.".format(item1.name))
                    return False
                item2 = l2[index]
                if item1.name != item2.name:
                    print("Items do not match: {} in l1, {} in l2".format(item1.name, item2.name))
                    return False
                index += 1
            if index != len(l2):
                print("l2 is long: Has extra items {}".format(l2[index:]))
                return False
            return True

        def compare_pb2_directories(d1, d2):
            result = (compare_list(d1.directories, d2.directories)
                    and compare_list(d1.symlinks, d2.symlinks)
                    and compare_list(d1.files, d2.files))
            return result
                        
        if not compare_pb2_directories(self.pb2_directory, other.pb2_directory):
            return False

        for d in self.pb2_directory.directories:
            self.index[d.name].buildstream_object.showdiff(other.index[d.name].buildstream_object)
        print("No differences found in {}".format(self))
              
    def show_files_recursive(self):
        elems = []
        for (k,v) in self.index.items():
            if type(v.pb_object) == remote_execution_pb2.DirectoryNode:
                elems.append("{}=[{}]".format(k, v.buildstream_object.show_files_recursive()))
            elif type(v.pb_object) == remote_execution_pb2.SymlinkNode:
                elems.append("{}(s)".format(k))
            elif type(v.pb_object) == remote_execution_pb2.FileNode:
                elems.append("{}(f)".format(k))
            else:
                elems.append("{}(?)".format(k))
        return " ".join(elems)
        
    def import_files(self, external_pathspec, *, files=None,
                     report_written=True, update_utimes=False,
                     can_link=False):
        """Imports some or all files from external_path into this directory.

        Keyword arguments: external_pathspec: Either a string
        containing a pathname, or a Directory object, to use as the
        source.

        files (list of strings): A list of all the files relative to
        the external_pathspec to copy. If 'None' is supplied, all
        files are copied.

        report_written (bool): Return the full list of files
        written. Defaults to true. If false, only a list of
        overwritten files is returned.

        update_utimes (bool): Currently ignored, since CAS does not store utimes.

        can_link (bool): Ignored, since hard links do not have any meaning within CAS.
        """

        print("Directory before import: {}".format(self.show_files_recursive()))

        # Sync self
        self._recalculate_recursing_down()
        if self.parent:
            self.parent._recalculate_recursing_up(self)
        
        # Duplicate the current directory

        
        print("Original CAS before CAS-based import: {}".format(self.show_files_recursive()))
        print("Original CAS hash: {}".format(self.ref.hash))
        duplicate_cas = None
        self._verify_unique()
        if isinstance(external_pathspec, CasBasedDirectory):
            duplicate_cas = CasBasedDirectory(self.context, ref=copy.copy(self.ref))
            duplicate_cas._verify_unique()
            print("-"*80 + "Performing direct CAS-to-CAS import")
            print("Duplicated CAS before file-based import: {}".format(duplicate_cas.show_files_recursive()))
            print("Duplicate CAS hash: {}".format(duplicate_cas.ref.hash))
            result = self._import_cas_into_cas(external_pathspec, files=files)
            self._verify_unique()
            print("Result of cas-to-cas import: {}".format(self.show_files_recursive()))
            print("-"*80 + "Performing round-trip import via file system")
            with tempfile.TemporaryDirectory(prefix="roundtrip") as tmpdir:
                external_pathspec.export_files(tmpdir)
                if files is None:
                    files = list_relative_paths(tmpdir)
                duplicate_cas._import_files_from_directory(tmpdir, files=files)
                duplicate_cas._recalculate_recursing_down()
                if duplicate_cas.parent:
                    duplicate_cas.parent._recalculate_recursing_up(duplicate_cas)
                print("Result of direct import: {}".format(duplicate_cas.show_files_recursive()))
               

        else:
            print("-"*80 + "Performing initial import")
            if isinstance(external_pathspec, FileBasedDirectory):
                source_directory = external_pathspec.get_underlying_directory()
            else:
                source_directory = external_pathspec
            if files is None:
                files = list_relative_paths(external_pathspec)
            result = self._import_files_from_directory(source_directory, files=files)

        # TODO: No notice is taken of report_written, update_utimes or can_link.
        # Current behaviour is to fully populate the report, which is inefficient,
        # but still correct.


        # We need to recalculate and store the hashes of all directories both
        # up and down the tree; we have changed our directory by importing files
        # which changes our hash and all our parents' hashes of us. The trees
        # lower down need to be stored in the CAS as they are not automatically
        # added during construction.
        self._recalculate_recursing_down()
        if self.parent:
            self.parent._recalculate_recursing_up(self)
        if duplicate_cas:
            if duplicate_cas.ref.hash != self.ref.hash:
                self.showdiff(duplicate_cas)
                raise VirtualDirectoryError("Mismatch between file-imported result {} and cas-to-cas imported result {}.".format(duplicate_cas.ref.hash,self.ref.hash))

        return result

    def set_deterministic_mtime(self):
        """ Sets a static modification time for all regular files in this directory.
        Since we don't store any modification time, we don't need to do anything.
        """
        pass

    def set_deterministic_user(self):
        """ Sets all files in this directory to the current user's euid/egid.
        We also don't store user data, so this can be ignored.
        """
        pass

    def export_files(self, to_directory, *, can_link=False, can_destroy=False):
        """Copies everything from this into to_directory, which must be the name
        of a traditional filesystem directory.

        Arguments:

        to_directory (string): a path outside this directory object
        where the contents will be copied to.

        can_link (bool): Whether we can create hard links in to_directory
        instead of copying.

        can_destroy (bool): Whether we can destroy elements in this
        directory to export them (e.g. by renaming them as the
        target).

        """

        if not os.path.exists(to_directory):
            os.mkdir(to_directory)

        for entry in self.pb2_directory.directories:
            if entry.name not in self.index:
                raise VirtualDirectoryError("CasDir {} contained {} in directories but not in the index"
                                            .format(str(self), entry.name))
            if not self._directory_read:
                raise VirtualDirectoryError("CasDir {} has not been indexed yet".format(str(self)))
            dest_dir = os.path.join(to_directory, entry.name)
            if not os.path.exists(dest_dir):
                os.mkdir(dest_dir)
            target = self.descend([entry.name])
            target.export_files(dest_dir)
        for entry in self.pb2_directory.files:
            # Extract the entry to a single file
            dest_name = os.path.join(to_directory, entry.name)
            src_name = self.cas_cache.objpath(entry.digest)
            safe_copy(src_name, dest_name)
            if entry.is_executable:
                os.chmod(dest_name, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                         stat.S_IRGRP | stat.S_IXGRP |
                         stat.S_IROTH | stat.S_IXOTH)
        for entry in self.pb2_directory.symlinks:
            src_name = os.path.join(to_directory, entry.name)
            target_name = entry.target
            print("Exporting symlink named {}".format(src_name))
            try:
                os.symlink(target_name, src_name)
            except FileExistsError as e:
                raise BstError(("Cannot create a symlink named {} pointing to {}." +
                                " The original error was: {}").
                               format(src_name, entry.target, e))

    def export_to_tar(self, tarfile, destination_dir, mtime=0):
        raise NotImplementedError()

    def mark_changed(self):
        """ It should not be possible to externally modify a CAS-based
        directory at the moment."""
        raise NotImplementedError()

    def is_empty(self):
        """ Return true if this directory has no files, subdirectories or links in it.
        """
        return len(self.index) == 0

    def _mark_directory_unmodified(self):
        # Marks all entries in this directory and all child directories as unmodified.
        for i in self.index.values():
            i.modified = False
            if isinstance(i.buildstream_object, CasBasedDirectory):
                i.buildstream_object._mark_directory_unmodified()

    def _mark_entry_unmodified(self, name):
        # Marks an entry as unmodified. If the entry is a directory, it will
        # recursively mark all its tree as unmodified.
        self.index[name].modified = False
        if self.index[name].buildstream_object:
            self.index[name].buildstream_object._mark_directory_unmodified()

    def mark_unmodified(self):
        """ Marks all files in this directory (recursively) as unmodified.
        If we have a parent, we mark our own entry as unmodified in that parent's
        index.
        """
        if self.parent:
            self.parent._mark_entry_unmodified(self._find_self_in_parent())
        else:
            self._mark_directory_unmodified()

    def list_modified_paths(self):
        """Provide a list of relative paths which have been modified since the
        last call to mark_unmodified.

        Return value: List(str) - list of modified paths
        """

        filelist = []
        for (k, v) in self.index.items():
            if isinstance(v.buildstream_object, CasBasedDirectory):
                filelist.extend([k + os.path.sep + x for x in v.buildstream_object.list_modified_paths()])
            elif isinstance(v.pb_object, remote_execution_pb2.FileNode) and v.modified:
                filelist.append(k)
        return filelist

    def _contains_only_directories(self):
        for (k, v) in self.index.items():
            if not isinstance(v.buildstream_object, CasBasedDirectory):
                return False
        return True

    def list_relative_paths(self, relpath=""):
        """Provide a list of all relative paths.

        NOTE: This list is not in the same order as utils.list_relative_paths.

        Return value: List(str) - list of all paths
        """

        print("Running list_relative_paths on relpath {}".format(relpath))
        symlink_list = list(filter(lambda i: isinstance(i[1].pb_object, remote_execution_pb2.SymlinkNode), self.index.items()))
        file_list = list(filter(lambda i: isinstance(i[1].pb_object, remote_execution_pb2.FileNode), self.index.items()))
        directory_list = list(filter(lambda i: isinstance(i[1].buildstream_object, CasBasedDirectory), self.index.items()))
        symlinks_to_directories_list = []
        print("Running list_relative_paths on relpath {}. files={}, symlinks={}".format(relpath, [f[0] for f in file_list], [s[0] for s in symlink_list]))

        for (k, v) in sorted(symlink_list):
            target = self._resolve(k, absolute_symlinks_resolve=True)
            if isinstance(target, CasBasedDirectory):
                symlinks_to_directories_list.append(k)
            else:
                # Broken symlinks are also considered files!
                file_list.append((k,v))
        for d in sorted(symlinks_to_directories_list):
            yield os.path.join(relpath, d)
        if file_list == [] and relpath != "":
            print("Yielding empty directory name {}".format(relpath))
            yield relpath
        else:
            for (k, v) in sorted(file_list):
                print("Yielding file {}".format(k))
                yield os.path.join(relpath, k)

        for (k, v) in sorted(directory_list):
            print("Yielding from subdirectory name {}".format(k))
            yield from v.buildstream_object.list_relative_paths(relpath=os.path.join(relpath, k))
        print("List_relative_paths on {} complete".format(relpath))

    def recalculate_hash(self):
        """ Recalcuates the hash for this directory and store the results in
        the cache. If this directory has a parent, tell it to
        recalculate (since changing this directory changes an entry in
        the parent). Hashes for subdirectories also get recalculated.
        """
        self._recalculate_recursing_up()
        self._recalculate_recursing_down()

    def _get_identifier(self):
        path = ""
        if self.parent:
            path = self.parent._get_identifier()
        if self.filename:
            path += "/" + self.filename
        else:
            path += "/" + self.common_name
        return path

    def __str__(self):
        return "[CAS:{}]".format(self._get_identifier())

    def _get_underlying_directory(self):
        """ There is no underlying directory for a CAS-backed directory, so
        throw an exception. """
        raise VirtualDirectoryError("_get_underlying_directory was called on a CAS-backed directory," +
                                    " which has no underlying directory.")
