import os
import pytest

import tests.testutils.patch as patch
from tests.testutils import cli_integration as cli
from tests.testutils.integration import assert_contains
from tests.testutils.site import IS_LINUX, NO_FUSE

pytestmark = pytest.mark.integration
pytestmark = pytest.mark.skipif(IS_LINUX and NO_FUSE, reason='FUSE not supported on this system')

DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), '..', '..', 'doc', 'examples', 'developing'
)


# Test that the project builds successfully
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_autotools_build(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    checkout = os.path.join(cli.directory, 'checkout')

    # Check that the project can be built correctly.
    result = cli.run(project=project, args=['build', 'hello.bst'])
    result.assert_success()

    result = cli.run(project=project, args=['checkout', 'hello.bst', checkout])
    result.assert_success()

    assert_contains(checkout, ['/usr', '/usr/lib', '/usr/bin',
                               '/usr/share', '/usr/lib/debug',
                               '/usr/lib/debug/hello', '/usr/bin/hello'])


# Test the unmodified hello command works as expected.
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_run_unmodified_hello(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    result = cli.run(project=project, args=['build', 'hello.bst'])
    result.assert_success()

    result = cli.run(project=project, args=['shell', 'hello.bst', 'hello'])
    result.assert_success()
    assert result.output == 'Hello World\n'


# Test opening a workspace
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_open_workspace(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    workspace_dir = os.path.join(str(tmpdir), "workspace_hello")

    result = cli.run(project=project, args=['workspace', 'open', '-f', 'hello.bst', workspace_dir])
    result.assert_success()

    result = cli.run(project=project, args=['workspace', 'list'])
    result.assert_success()

    result = cli.run(project=project, args=['workspace', 'close', '--remove-dir', 'hello.bst'])
    result.assert_success()


# Test making a change using the workspace
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_make_change_in_workspace(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    workspace_dir = os.path.join(str(tmpdir), "workspace_hello")

    result = cli.run(project=project, args=['workspace', 'open', '-f', 'hello.bst', workspace_dir])
    result.assert_success()

    result = cli.run(project=project, args=['workspace', 'list'])
    result.assert_success()

    patch_target = os.path.join(workspace_dir, "hello.c")
    patch_source = os.path.join(project, "update.patch")
    patch.apply(patch_target, patch_source)

    result = cli.run(project=project, args=['build', 'hello.bst'])
    result.assert_success()

    result = cli.run(project=project, args=['shell', 'hello.bst', '--', 'hello'])
    result.assert_success()
    assert result.output == 'Hello World\nWe can use workspaces!\n'

    result = cli.run(project=project, args=['workspace', 'close', '--remove-dir', 'hello.bst'])
    result.assert_success()
