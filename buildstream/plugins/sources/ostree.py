#!/usr/bin/env python3
#
#  Copyright (C) 2016 Codethink Limited
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
#        Andrew Leeming <andrew.leeming@codethink.co.uk>

"""A Source implementation for importing/staging of OSTree checkouts.

**Usage:**

.. code:: yaml

   # Specify the ostree source kind
   kind: ostree

   # Optionally specify a relative staging directory
   # directory: path/to/stage

   # Specify the repository url, using an alias defined
   # in your project configuration is recommended.
   url: upstream:runtime

   # Optionally specify a symbolic tracking branch or tag, this
   # will be used to update the 'ref' when refreshing the pipeline.
   track: runtime/x86_64/stable

   # Specify the commit checksum, this must be specified in order
   # to checkout sources and build, but can be automatically
   # updated if the 'track' attribute was specified.
   ref: d63cbb6fdc0bbdadc4a1b92284826a6d63a7ebcd

   # For signed ostree repositories, specify a local project relative
   # path to the public verifying GPG key for this remote.
   gpg-key: keys/runtime.gpg
"""

import os
import tempfile
import shutil

from buildstream import Source, SourceError, Consistency
from buildstream import utils
from buildstream import _ostree
from buildstream._ostree import OSTreeError


class OSTreeSource(Source):

    def configure(self, node):
        project = self.get_project()

        self.node_validate(node, ['kind', 'url', 'ref', 'track', 'gpg-key'])

        self.original_url = self.node_get_member(node, str, 'url')
        self.url = project.translate_url(self.original_url)
        self.ref = self.node_get_member(node, str, 'ref', '') or None
        self.tracking = self.node_get_member(node, str, 'track', '') or None
        self.mirror = os.path.join(self.get_mirror_directory(),
                                   utils.url_directory_name(self.url))

        # (optional) Not all repos are signed. But if they are, get the gpg key
        self.gpg_key = self.node_get_member(node, str, 'gpg-key', '') or None
        self.gpg_key_path = None
        if self.gpg_key is not None:
            self.gpg_key_path = os.path.join(project.directory, self.gpg_key)

        # Our OSTree repo handle
        self.repo = None

        if not (self.ref or self.tracking):
            raise SourceError("Must specify either 'ref' or 'track' parameters")

    def preflight(self):
        return

    def get_unique_key(self):
        return [self.original_url, self.ref]

    def get_ref(self):
        return self.ref

    def set_ref(self, ref, node):
        node['ref'] = self.ref = ref

    def track(self):
        # If self.tracking is not specified its' not an error, just silently return
        if not self.tracking:
            return None

        self.ensure()
        with self.timed_activity("Fetching tracking ref '{}' from origin: {}"
                                 .format(self.tracking, self.url)):
            try:
                _ostree.fetch(self.repo, ref=self.tracking, progress=self.progress)
            except OSTreeError as e:
                raise SourceError("{}: Failed to fetch tracking ref '{}' from origin {}\n\n{}"
                                  .format(self, self.tracking, self.url, e)) from e

        return _ostree.checksum(self.repo, self.tracking)

    def fetch(self):
        self.ensure()
        if not _ostree.exists(self.repo, self.ref):
            with self.timed_activity("Fetching remote ref: {} from origin: {}"
                                     .format(self.ref, self.url)):
                try:
                    _ostree.fetch(self.repo, ref=self.ref, progress=self.progress)
                except OSTreeError as e:
                    raise SourceError("{}: Failed to fetch ref '{}' from origin: {}\n\n{}"
                                      .format(self, self.ref, self.url, e)) from e

    def stage(self, directory):
        self.ensure()

        # Checkout self.ref into the specified directory
        with self.tempdir() as tmpdir:
            checkoutdir = os.path.join(tmpdir, 'checkout')

            with self.timed_activity("Staging ref: {} from origin: {}"
                                     .format(self.ref, self.url)):
                try:
                    _ostree.checkout(self.repo, checkoutdir, self.ref, user=True)
                except OSTreeError as e:
                    raise SourceError("{}: Failed to checkout ref '{}' from origin: {}\n\n{}"
                                      .format(self, self.ref, self.url, e)) from e

            # The target directory is guaranteed to exist, here we must move the
            # content of out checkout into the existing target directory.
            #
            # We may not be able to create the target directory as it's parent
            # may be readonly, and the directory itself is often a mount point.
            #
            try:
                for entry in os.listdir(checkoutdir):
                    source_path = os.path.join(checkoutdir, entry)
                    shutil.move(source_path, directory)
            except (shutil.Error, OSError) as e:
                raise SourceError("{}: Failed to move ostree checkout {} from '{}' to '{}'\n\n{}"
                                  .format(self, self.url, tmpdir, directory, e)) from e

    def get_consistency(self):
        if self.ref is None:
            return Consistency.INCONSISTENT

        self.ensure()
        if _ostree.exists(self.repo, self.ref):
            return Consistency.CACHED
        return Consistency.RESOLVED

    #
    # Local helpers
    #
    def ensure(self):
        if not self.repo:
            self.status("Creating local mirror for {}".format(self.url))

            self.repo = _ostree.ensure(self.mirror, True)
            gpg_key = None
            if self.gpg_key_path:
                gpg_key = 'file://' + self.gpg_key_path

            try:
                _ostree.configure_remote(self.repo, "origin", self.url, key_url=gpg_key)
            except OSTreeError as e:
                raise SourceError("{}: Failed to configure origin {}\n\n{}".format(self, self.url, e)) from e

    def progress(self, percent, message):
        self.status(message)


# Plugin entry point
def setup():
    return OSTreeSource
