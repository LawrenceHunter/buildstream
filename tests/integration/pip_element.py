import os

import pytest

from buildstream2 import _yaml

from buildstream2.testing import cli_integration as cli
from buildstream2.testing.integration import assert_contains
from tests.testutils.site import HAVE_SANDBOX


pytestmark = pytest.mark.integration


DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project"
)


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.skipif(not HAVE_SANDBOX, reason='Only available with a functioning sandbox')
def test_pip_build(cli, datafiles):
    project = str(datafiles)
    checkout = os.path.join(cli.directory, 'checkout')
    element_path = os.path.join(project, 'elements')
    element_name = 'pip/hello.bst'

    element = {
        'kind': 'pip',
        'variables': {
            'pip': 'pip3'
        },
        'depends': [{
            'filename': 'base.bst'
        }],
        'sources': [{
            'kind': 'tar',
            'url': 'file://{}/files/hello.tar.xz'.format(project),
            'ref': 'ad96570b552498807abec33c06210bf68378d854ced6753b77916c5ed517610d'

        }]
    }
    os.makedirs(os.path.dirname(os.path.join(element_path, element_name)), exist_ok=True)
    _yaml.dump(element, os.path.join(element_path, element_name))

    result = cli.run(project=project, args=['build', element_name])
    assert result.exit_code == 0

    result = cli.run(project=project, args=['artifact', 'checkout', element_name, '--directory', checkout])
    assert result.exit_code == 0

    assert_contains(checkout, ['/usr', '/usr/lib', '/usr/bin',
                               '/usr/bin/hello', '/usr/lib/python3.6'])


# Test running an executable built with pip
@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.skipif(not HAVE_SANDBOX, reason='Only available with a functioning sandbox')
def test_pip_run(cli, datafiles):
    # Create and build our test element
    test_pip_build(cli, datafiles)

    project = str(datafiles)
    element_name = 'pip/hello.bst'

    result = cli.run(project=project, args=['shell', element_name, '/usr/bin/hello'])
    assert result.exit_code == 0
    assert result.output == 'Hello, world!\n'
