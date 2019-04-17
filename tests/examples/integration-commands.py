# Pylint doesn't play well with fixtures and dependency injection from pytest
# pylint: disable=redefined-outer-name

import os
import pytest

from buildstream2.testing import cli_integration as cli  # pylint: disable=unused-import
from tests.testutils.site import HAVE_BWRAP, IS_LINUX, MACHINE_ARCH


pytestmark = pytest.mark.integration
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), '..', '..', 'doc', 'examples', 'integration-commands'
)


@pytest.mark.skipif(MACHINE_ARCH != 'x86-64',
                    reason='Examples are written for x86-64')
@pytest.mark.skipif(not IS_LINUX or not HAVE_BWRAP, reason='Only available on linux with bubblewrap')
@pytest.mark.datafiles(DATA_DIR)
def test_integration_commands_build(cli, datafiles):
    project = str(datafiles)

    result = cli.run(project=project, args=['build', 'hello.bst'])
    assert result.exit_code == 0


# Test running the executable
@pytest.mark.skipif(MACHINE_ARCH != 'x86-64',
                    reason='Examples are written for x86-64')
@pytest.mark.skipif(not IS_LINUX or not HAVE_BWRAP, reason='Only available on linux with bubblewrap')
@pytest.mark.datafiles(DATA_DIR)
def test_integration_commands_run(cli, datafiles):
    project = str(datafiles)

    result = cli.run(project=project, args=['build', 'hello.bst'])
    assert result.exit_code == 0

    result = cli.run(project=project, args=['shell', 'hello.bst', '--', 'hello', 'pony'])
    assert result.exit_code == 0
    assert result.output == 'Hello pony\n'
