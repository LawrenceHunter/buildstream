import os
import pytest

from tests.testutils import cli_integration as cli
from tests.testutils.integration import assert_contains
from tests.testutils.site import IS_LINUX, NO_FUSE

pytestmark = pytest.mark.integration
pytestmark = pytest.mark.skipif(IS_LINUX and NO_FUSE, reason='FUSE not supported on this system')

DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), '..', '..', 'doc', 'examples', 'junctions'
)


# Test that the project builds successfully
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_build(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    result = cli.run(project=project, args=['build', 'callHello.bst'])
    result.assert_success()


# Test the callHello script works as expected.
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_shell_call_hello(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    result = cli.run(project=project, args=['build', 'callHello.bst'])
    result.assert_success()

    result = cli.run(project=project, args=['shell', 'callHello.bst', '--', '/bin/sh', 'callHello.sh'])
    result.assert_success()
    assert result.output == 'Calling hello:\nHello World!\nThis is amhello 1.0.\n'


# Test opening a cross-junction workspace
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_open_cross_junction_workspace(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    workspace_dir = os.path.join(str(tmpdir), "workspace_hello_junction")

    result = cli.run(project=project,
                     args=['workspace', 'open', 'hello-junction.bst:hello.bst', workspace_dir])
    result.assert_success()

    result = cli.run(project=project,
                     args=['workspace', 'close', '--remove-dir', 'hello-junction.bst:hello.bst'])
    result.assert_success()
