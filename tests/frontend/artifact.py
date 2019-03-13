#
#  Copyright (C) 2018 Codethink Limited
#  Copyright (C) 2018 Bloomberg Finance LP
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
#  Authors: Richard Maw <richard.maw@codethink.co.uk>
#

import os
import pytest

from buildstream.plugintestutils import cli
from tests.testutils import create_artifact_share


# Project directory
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project",
)


@pytest.mark.datafiles(DATA_DIR)
def test_artifact_log(cli, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    # Get the cache key of our test element
    result = cli.run(project=project, silent=True, args=[
        '--no-colors',
        'show', '--deps', 'none', '--format', '%{full-key}',
        'target.bst'
    ])
    key = result.output.strip()

    # Ensure we have an artifact to read
    result = cli.run(project=project, args=['build', 'target.bst'])
    assert result.exit_code == 0

    # Read the log via the element name
    result = cli.run(project=project, args=['artifact', 'log', 'target.bst'])
    assert result.exit_code == 0
    log = result.output

    # Read the log via the key
    result = cli.run(project=project, args=['artifact', 'log', 'test/target/' + key])
    assert result.exit_code == 0
    assert log == result.output

    # Read the log via glob
    result = cli.run(project=project, args=['artifact', 'log', 'test/target/*'])
    assert result.exit_code == 0
    # The artifact is cached under both a strong key and a weak key
    assert (log + log) == result.output


# Test that we can delete the artifact of the element which corresponds
# to the current project state
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_delete_element(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element = 'target.bst'

    # Build the element and ensure it's cached
    result = cli.run(project=project, args=['build', element])
    result.assert_success()
    assert cli.get_element_state(project, element) == 'cached'

    result = cli.run(project=project, args=['artifact', 'delete', element])
    result.assert_success()
    assert cli.get_element_state(project, element) != 'cached'


# Test that we can delete an artifact by specifying its ref.
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_delete_artifact(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element = 'target.bst'

    # Configure a local cache
    local_cache = os.path.join(str(tmpdir), 'artifacts')
    cli.configure({'cachedir': local_cache})

    # First build an element so that we can find its artifact
    result = cli.run(project=project, args=['build', element])
    result.assert_success()

    # Obtain the artifact ref
    cache_key = cli.get_element_key(project, element)
    artifact = os.path.join('test', os.path.splitext(element)[0], cache_key)

    # Explicitly check that the ARTIFACT exists in the cache
    assert os.path.exists(os.path.join(local_cache, 'cas', 'refs', 'heads', artifact))

    # Delete the artifact
    result = cli.run(project=project, args=['artifact', 'delete', artifact])
    result.assert_success()

    # Check that the ARTIFACT is no longer in the cache
    assert not os.path.exists(os.path.join(local_cache, 'cas', 'refs', 'heads', artifact))


# Test the `bst artifact delete` command with multiple, different arguments.
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_delete_element_and_artifact(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element = 'target.bst'
    dep = 'compose-all.bst'

    # Configure a local cache
    local_cache = os.path.join(str(tmpdir), 'artifacts')
    cli.configure({'cachedir': local_cache})

    # First build an element so that we can find its artifact
    result = cli.run(project=project, args=['build', element])
    result.assert_success()
    assert cli.get_element_state(project, element) == 'cached'
    assert cli.get_element_state(project, dep) == 'cached'

    # Obtain the artifact ref
    cache_key = cli.get_element_key(project, element)
    artifact = os.path.join('test', os.path.splitext(element)[0], cache_key)

    # Explicitly check that the ARTIFACT exists in the cache
    assert os.path.exists(os.path.join(local_cache, 'cas', 'refs', 'heads', artifact))

    # Delete the artifact
    result = cli.run(project=project, args=['artifact', 'delete', artifact, dep])
    result.assert_success()

    # Check that the ARTIFACT is no longer in the cache
    assert not os.path.exists(os.path.join(local_cache, 'cas', 'refs', 'heads', artifact))

    # Check that the dependency ELEMENT is no longer cached
    assert cli.get_element_state(project, dep) != 'cached'


# Test that we receive the appropriate stderr when we try to delete an artifact
# that is not present in the cache.
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_delete_unbuilt_artifact(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element = 'target.bst'

    # delete it, just in case it's there
    _ = cli.run(project=project, args=['artifact', 'delete', element])

    # Ensure the element is not cached
    assert cli.get_element_state(project, element) != 'cached'

    # Now try and remove it again (now we know its not there)
    result = cli.run(project=project, args=['artifact', 'delete', element])

    cache_key = cli.get_element_key(project, element)
    artifact = os.path.join('test', os.path.splitext(element)[0], cache_key)
    expected_err = "WARNING Could not find ref '{}'".format(artifact)
    assert expected_err in result.stderr
