import os
import pytest

from buildstream import _yaml
from buildstream._exceptions import ErrorDomain

from tests.testutils import cli_integration as cli, create_artifact_share
from tests.testutils.site import IS_LINUX


pytestmark = pytest.mark.integration


DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project"
)


@pytest.mark.datafiles(DATA_DIR)
def test_build_checkout_cached_fail(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = os.path.join(project, 'elements', 'element.bst')
    workspace = os.path.join(cli.directory, 'workspace')
    checkout = os.path.join(cli.directory, 'checkout')

    # Write out our test target
    element = {
        'kind': 'script',
        'depends': [
            {
                'filename': 'base.bst',
                'type': 'build',
            },
        ],
        'config': {
            'commands': [
                'touch %{install-root}/foo',
                'false',
            ],
        },
    }
    _yaml.dump(element, element_path)

    # Try to build it, this should result in a failure that contains the content
    result = cli.run(project=project, args=['build', 'element.bst'])
    result.assert_main_error(ErrorDomain.STREAM, None)

    # Assert that it's cached in a failed artifact
    assert cli.get_element_state(project, 'element.bst') == 'failed'

    # Now check it out
    result = cli.run(project=project, args=[
        'checkout', 'element.bst', checkout
    ])
    result.assert_success()

    # Check that the checkout contains the file created before failure
    filename = os.path.join(checkout, 'foo')
    assert os.path.exists(filename)


@pytest.mark.datafiles(DATA_DIR)
def test_build_depend_on_cached_fail(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    dep_path = os.path.join(project, 'elements', 'dep.bst')
    target_path = os.path.join(project, 'elements', 'target.bst')
    workspace = os.path.join(cli.directory, 'workspace')
    checkout = os.path.join(cli.directory, 'checkout')

    dep = {
        'kind': 'script',
        'depends': [
            {
                'filename': 'base.bst',
                'type': 'build',
            },
        ],
        'config': {
            'commands': [
                'touch %{install-root}/foo',
                'false',
            ],
        },
    }
    _yaml.dump(dep, dep_path)
    target = {
        'kind': 'script',
        'depends': [
            {
                'filename': 'base.bst',
                'type': 'build',
            },
            {
                'filename': 'dep.bst',
                'type': 'build',
            },
        ],
        'config': {
            'commands': [
                'test -e /foo',
            ],
        },
    }
    _yaml.dump(target, target_path)

    # Try to build it, this should result in caching a failure to build dep
    result = cli.run(project=project, args=['build', 'dep.bst'])
    result.assert_main_error(ErrorDomain.STREAM, None)

    # Assert that it's cached in a failed artifact
    assert cli.get_element_state(project, 'dep.bst') == 'failed'

    # Now we should fail because we've a cached fail of dep
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_main_error(ErrorDomain.STREAM, None)

    # Assert that it's not yet built, since one of its dependencies isn't ready.
    assert cli.get_element_state(project, 'target.bst') == 'waiting'


@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize("on_error", ("continue",))
def test_push_cached_fail(cli, tmpdir, datafiles, on_error):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = os.path.join(project, 'elements', 'element.bst')
    workspace = os.path.join(cli.directory, 'workspace')
    checkout = os.path.join(cli.directory, 'checkout')

    # Write out our test target
    element = {
        'kind': 'script',
        'depends': [
            {
                'filename': 'base.bst',
                'type': 'build',
            },
        ],
        'config': {
            'commands': [
                'false',
            ],
        },
    }
    _yaml.dump(element, element_path)

    with create_artifact_share(os.path.join(str(tmpdir), 'remote')) as share:
        cli.configure({
            'artifacts': {'url': share.repo, 'push': True},
        })

        # Build the element, continuing to finish active jobs on error.
        result = cli.run(project=project, args=['--on-error={}'.format(on_error), 'build', 'element.bst'])
        result.assert_main_error(ErrorDomain.STREAM, None)

        # This element should have failed
        assert cli.get_element_state(project, 'element.bst') == 'failed'
        # This element should have been pushed to the remote
        assert share.has_artifact('test', 'element.bst', cli.get_element_key(project, 'element.bst'))
