#
#  Copyright (C) 2019 Codethink Limited
#  Copyright (C) 2019 Bloomberg Finance LP
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

import os
from collections import OrderedDict
from . import _sourcetests
from .repo import Repo
from .runcli import cli, cli_integration, cli_remote_execution

# To make use of these test utilities it is necessary to have pytest
# available. However, we don't want to have a hard dependency on
# pytest.
try:
    import pytest
except ImportError:
    module_name = globals()['__name__']
    msg = "Could not import pytest:\n" \
          "To use the {} module, you must have pytest installed.".format(module_name)
    raise ImportError(msg)


ALL_REPO_KINDS = OrderedDict()


def create_repo(kind, directory, subdir='repo'):
    """Convenience method for creating a Repo

    Args:
        kind (str): The kind of repo to create (a source plugin basename)
        directory (str): The path where the repo will keep a cache

    Returns:
        (Repo): A new Repo object
    """
    try:
        constructor = ALL_REPO_KINDS[kind]
    except KeyError as e:
        raise AssertionError("Unsupported repo kind {}".format(kind)) from e

    return constructor(directory, subdir=subdir)


def register_repo_kind(kind, cls):
    """ Register a new repo kind on which to run the generic source tests.

    Args:
       kind (str): The kind of repo to create (a source plugin basename)
       cls (cls) : A class derived from Repo.
    """
    ALL_REPO_KINDS[kind] = cls


def sourcetests_collection_hook(session):
    """ Used to hook the templated source plugin tests into a pyest test suite.

    This should be called via the `pytest_sessionstart
    hook<https://docs.pytest.org/en/latest/reference.html#collection-hooks>`.
    The tests in the _sourcetests package will be collected as part of
    whichever test package this hook is called from.

    Args:
        session (pytest.Session): The current pytest session
    """
    SOURCE_TESTS_PATH = os.path.dirname(_sourcetests.__file__)
    # Add the location of the source tests to the session's
    # python_files config. Without this, pytest may filter out these
    # tests during collection.
    session.config.addinivalue_line("python_files", os.path.join(SOURCE_TESTS_PATH, "/**.py"))
    session.config.args.append(SOURCE_TESTS_PATH)
