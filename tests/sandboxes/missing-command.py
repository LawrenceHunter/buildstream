import os
import pytest

from buildstream2._exceptions import ErrorDomain

from buildstream2.testing import cli


DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "missing-command"
)


@pytest.mark.datafiles(DATA_DIR)
def test_missing_command(cli, datafiles):
    project = str(datafiles)
    result = cli.run(project=project, args=['build', 'no-runtime.bst'])
    result.assert_task_error(ErrorDomain.SANDBOX, 'missing-command')
