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
#  Authors: Tristan Van Berkom <tristan.vanberkom@codethink.co.uk>
#           Sam Thursfield <sam.thursfield@codethink.co.uk>
#           Jürg Billeter <juerg.billeter@codethink.co.uk>
#

import os
import pytest

from buildstream._exceptions import ErrorDomain
from tests.testutils import cli, create_artifact_share, create_element_size
from tests.testutils import generate_junction, wait_for_cache_granularity
from . import configure_project


# Project directory
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project",
)

# Credential directory
CRED_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "creds",
)

# Parameters for credentials
CREDENTIAL_FILENAMES = {
    'unsecured': {},

    'server_secured': {
        'server_key': 'server_key.pem',
        'server_cert': 'server_cert.pem',
    },
}


# Assert that a given artifact is in the share
#
def assert_shared(cli, share, project, element_name):
    # NOTE: 'test' here is the name of the project
    # specified in the project.conf we are testing with.
    #
    cache_key = cli.get_element_key(project, element_name)
    if not share.has_artifact('test', element_name, cache_key):
        raise AssertionError("Artifact share at {} does not contain the expected element {}"
                             .format(share.repo, element_name))


# Assert that a given artifact is NOT in the share
#
def assert_not_shared(cli, share, project, element_name):
    # NOTE: 'test' here is the name of the project
    # specified in the project.conf we are testing with.
    #
    cache_key = cli.get_element_key(project, element_name)
    if share.has_artifact('test', element_name, cache_key):
        raise AssertionError("Artifact share at {} unexpectedly contains the element {}"
                             .format(share.repo, element_name))


# Taking a dictionary of filenames, this returns a dictionary of qualified
# fielnames
def join_credentials_path(credential_filenames, credential_files):
    return {
        key: os.path.join(credential_files, filename)
        for key, filename in credential_filenames.items()
    }


# Adds the server certificate to the configuration if it exists and returns
# this for ease of use
def add_client_config_creds(configuration, credentials):
    if 'server_cert' in credentials:
        artifacts = configuration['artifacts']
        if isinstance(artifacts, (list,)):
            for subconfig in artifacts:
                subconfig['server-cert'] = credentials['server_cert']
        else:
            artifacts['server-cert'] = credentials['server_cert']
    return configuration


# Tests that:
#
#  * `bst push` fails if there are no remotes configured for pushing
#  * `bst push` successfully pushes to any remote that is configured for pushing
#
@pytest.mark.parametrize(
    'credential_filenames', CREDENTIAL_FILENAMES.values(), ids=list(CREDENTIAL_FILENAMES))
@pytest.mark.datafiles(DATA_DIR, CRED_DIR, keep_top_dir=True)
def test_push(cli, tmpdir, datafiles, credential_filenames):
    project = os.path.join(datafiles, 'project')
    credfiles = os.path.join(datafiles, 'creds')

    credentials = join_credentials_path(credential_filenames, credfiles)

    # First build the project without the artifact cache configured
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()

    # Assert that we are now cached locally
    assert cli.get_element_state(project, 'target.bst') == 'cached'

    # Set up two artifact shares.
    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare1'),
                               credentials=credentials) as share1:

        with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2'),
                                   credentials=credentials) as share2:

            # Try pushing with no remotes configured. This should fail.
            result = cli.run(project=project, args=['push', 'target.bst'])
            result.assert_main_error(ErrorDomain.STREAM, None)

            # Configure bst to pull but not push from a cache and run `bst push`.
            # This should also fail.
            cli.configure(add_client_config_creds({
                'artifacts': {'url': share1.repo, 'push': False},
            }, credentials))
            result = cli.run(project=project, args=['push', 'target.bst'])
            result.assert_main_error(ErrorDomain.STREAM, None)

            # Configure bst to push to one of the caches and run `bst push`. This works.
            cli.configure(add_client_config_creds({
                'artifacts': [
                    {'url': share1.repo, 'push': False},
                    {'url': share2.repo, 'push': True},
                ]
            }, credentials))
            result = cli.run(project=project, args=['push', 'target.bst'])

            assert_not_shared(cli, share1, project, 'target.bst')
            assert_shared(cli, share2, project, 'target.bst')

        # Now try pushing to both

        with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2')) as share2:
            cli.configure(add_client_config_creds({
                'artifacts': [
                    {'url': share1.repo, 'push': True},
                    {'url': share2.repo, 'push': True},
                ]
            }, credentials))
            result = cli.run(project=project, args=['push', 'target.bst'])

            assert_shared(cli, share1, project, 'target.bst')
            assert_shared(cli, share2, project, 'target.bst')


# Tests that `bst push --deps all` pushes all dependencies of the given element.
#
@pytest.mark.parametrize(
    'credential_filenames', CREDENTIAL_FILENAMES.values(), ids=list(CREDENTIAL_FILENAMES))
@pytest.mark.datafiles(DATA_DIR, CRED_DIR, keep_top_dir=True)
def test_push_all(cli, tmpdir, datafiles, credential_filenames):
    project = os.path.join(datafiles, 'project')
    credfiles = os.path.join(datafiles, 'creds')

    credentials = join_credentials_path(credential_filenames, credfiles)

    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'), credentials=credentials) as share:

        # First build it without the artifact cache configured
        result = cli.run(project=project, args=['build', 'target.bst'])
        result.assert_success()

        # Assert that we are now cached locally
        assert cli.get_element_state(project, 'target.bst') == 'cached'

        # Configure artifact share
        cli.configure(add_client_config_creds({
            #
            # FIXME: This test hangs "sometimes" if we allow
            #        concurrent push.
            #
            #        It's not too bad to ignore since we're
            #        using the local artifact cache functionality
            #        only, but it should probably be fixed.
            #
            'scheduler': {
                'pushers': 1
            },
            'artifacts': {
                'url': share.repo,
                'push': True,
            }
        }, credentials))

        # Now try bst push all the deps
        result = cli.run(project=project, args=[
            'push', 'target.bst',
            '--deps', 'all'
        ])
        result.assert_success()

        # And finally assert that all the artifacts are in the share
        assert_shared(cli, share, project, 'target.bst')
        assert_shared(cli, share, project, 'import-bin.bst')
        assert_shared(cli, share, project, 'import-dev.bst')
        assert_shared(cli, share, project, 'compose-all.bst')


# Tests that `bst build` won't push artifacts to the cache it just pulled from.
#
# Regression test for https://gitlab.com/BuildStream/buildstream/issues/233.
@pytest.mark.datafiles(DATA_DIR)
def test_push_after_pull(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    # Set up two artifact shares.
    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare1')) as share1,\
        create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2')) as share2:

        # Set the scene: share1 has the artifact, share2 does not.
        #
        cli.configure({
            'artifacts': {'url': share1.repo, 'push': True},
        })

        result = cli.run(project=project, args=['build', 'target.bst'])
        result.assert_success()

        cli.remove_artifact_from_cache(project, 'target.bst')

        assert_shared(cli, share1, project, 'target.bst')
        assert_not_shared(cli, share2, project, 'target.bst')
        assert cli.get_element_state(project, 'target.bst') != 'cached'

        # Now run the build again. Correct `bst build` behaviour is to download the
        # artifact from share1 but not push it back again.
        #
        result = cli.run(project=project, args=['build', 'target.bst'])
        result.assert_success()
        assert result.get_pulled_elements() == ['target.bst']
        assert result.get_pushed_elements() == []

        # Delete the artifact locally again.
        cli.remove_artifact_from_cache(project, 'target.bst')

        # Now we add share2 into the mix as a second push remote. This time,
        # `bst build` should push to share2 after pulling from share1.
        cli.configure({
            'artifacts': [
                {'url': share1.repo, 'push': True},
                {'url': share2.repo, 'push': True},
            ]
        })
        result = cli.run(project=project, args=['build', 'target.bst'])
        result.assert_success()
        assert result.get_pulled_elements() == ['target.bst']
        assert result.get_pushed_elements() == ['target.bst']


# Ensure that when an artifact's size exceeds available disk space
# the least recently pushed artifact is deleted in order to make room for
# the incoming artifact.
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_expires(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = 'elements'

    # Create an artifact share (remote artifact cache) in the tmpdir/artifactshare
    # Mock a file system with 12 MB free disk space
    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'),
                               total_space=int(10e9), free_space=(int(12e6) + int(2e9))) as share:

        # Configure bst to push to the cache
        cli.configure({
            'artifacts': {'url': share.repo, 'push': True},
        })

        # Create and build an element of 5 MB
        create_element_size('element1.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element1.bst'])
        result.assert_success()

        # Create and build an element of 5 MB
        create_element_size('element2.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element2.bst'])
        result.assert_success()

        # check that element's 1 and 2 are cached both locally and remotely
        assert cli.get_element_state(project, 'element1.bst') == 'cached'
        assert_shared(cli, share, project, 'element1.bst')
        assert cli.get_element_state(project, 'element2.bst') == 'cached'
        assert_shared(cli, share, project, 'element2.bst')

        # Create and build another element of 5 MB (This will exceed the free disk space available)
        create_element_size('element3.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element3.bst'])
        result.assert_success()

        # Ensure it is cached both locally and remotely
        assert cli.get_element_state(project, 'element3.bst') == 'cached'
        assert_shared(cli, share, project, 'element3.bst')

        # Ensure element1 has been removed from the share
        assert_not_shared(cli, share, project, 'element1.bst')
        # Ensure that elemen2 remains
        assert_shared(cli, share, project, 'element2.bst')


# Test that a large artifact, whose size exceeds the quota, is not pushed
# to the remote share
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_too_large(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = 'elements'

    # Create an artifact share (remote cache) in tmpdir/artifactshare
    # Mock a file system with 5 MB total space
    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'),
                               total_space=int(5e6) + int(2e9)) as share:

        # Configure bst to push to the remote cache
        cli.configure({
            'artifacts': {'url': share.repo, 'push': True},
        })

        # Create and push a 3MB element
        create_element_size('small_element.bst', project, element_path, [], int(3e6))
        result = cli.run(project=project, args=['build', 'small_element.bst'])
        result.assert_success()

        # Create and try to push a 6MB element.
        create_element_size('large_element.bst', project, element_path, [], int(6e6))
        result = cli.run(project=project, args=['build', 'large_element.bst'])
        result.assert_success()

        # Ensure that the small artifact is still in the share
        assert cli.get_element_state(project, 'small_element.bst') == 'cached'
        assert_shared(cli, share, project, 'small_element.bst')

        # Ensure that the artifact is cached locally but NOT remotely
        assert cli.get_element_state(project, 'large_element.bst') == 'cached'
        assert_not_shared(cli, share, project, 'large_element.bst')


# Test that when an element is pulled recently, it is not considered the LRU element.
@pytest.mark.datafiles(DATA_DIR)
def test_recently_pulled_artifact_does_not_expire(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = 'elements'

    # Create an artifact share (remote cache) in tmpdir/artifactshare
    # Mock a file system with 12 MB free disk space
    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'),
                               total_space=int(10e9), free_space=(int(12e6) + int(2e9))) as share:

        # Configure bst to push to the cache
        cli.configure({
            'artifacts': {'url': share.repo, 'push': True},
        })

        # Create and build 2 elements, each of 5 MB.
        create_element_size('element1.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element1.bst'])
        result.assert_success()

        create_element_size('element2.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element2.bst'])
        result.assert_success()

        # Ensure they are cached locally
        assert cli.get_element_state(project, 'element1.bst') == 'cached'
        assert cli.get_element_state(project, 'element2.bst') == 'cached'

        # Ensure that they have  been pushed to the cache
        assert_shared(cli, share, project, 'element1.bst')
        assert_shared(cli, share, project, 'element2.bst')

        # Remove element1 from the local cache
        cli.remove_artifact_from_cache(project, 'element1.bst')
        assert cli.get_element_state(project, 'element1.bst') != 'cached'

        # Pull the element1 from the remote cache (this should update its mtime)
        result = cli.run(project=project, args=['pull', 'element1.bst', '--remote',
                                                share.repo])
        result.assert_success()

        # Ensure element1 is cached locally
        assert cli.get_element_state(project, 'element1.bst') == 'cached'

        wait_for_cache_granularity()

        # Create and build the element3 (of 5 MB)
        create_element_size('element3.bst', project, element_path, [], int(5e6))
        result = cli.run(project=project, args=['build', 'element3.bst'])
        result.assert_success()

        # Make sure it's cached locally and remotely
        assert cli.get_element_state(project, 'element3.bst') == 'cached'
        assert_shared(cli, share, project, 'element3.bst')

        # Ensure that element2 was deleted from the share and element1 remains
        assert_not_shared(cli, share, project, 'element2.bst')
        assert_shared(cli, share, project, 'element1.bst')


@pytest.mark.datafiles(DATA_DIR)
def test_push_cross_junction(cli, tmpdir, datafiles):
    project = str(datafiles)
    subproject_path = os.path.join(project, 'files', 'sub-project')
    junction_path = os.path.join(project, 'elements', 'junction.bst')

    generate_junction(tmpdir, subproject_path, junction_path, store_ref=True)

    result = cli.run(project=project, args=['build', 'junction.bst:import-etc.bst'])
    result.assert_success()

    assert cli.get_element_state(project, 'junction.bst:import-etc.bst') == 'cached'

    with create_artifact_share(os.path.join(str(tmpdir), 'artifactshare')) as share:
        cli.configure({
            'artifacts': {'url': share.repo, 'push': True},
        })
        result = cli.run(project=project, args=['push', 'junction.bst:import-etc.bst'])

        cache_key = cli.get_element_key(project, 'junction.bst:import-etc.bst')
        assert share.has_artifact('subtest', 'import-etc.bst', cache_key)
