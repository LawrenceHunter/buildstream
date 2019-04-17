# Pylint doesn't play well with fixtures and dependency injection from pytest
# pylint: disable=redefined-outer-name

import os
import re

import pytest

from buildstream2.testing import create_repo

from buildstream2 import _yaml
from buildstream2._exceptions import ErrorDomain
from buildstream2.testing import cli  # pylint: disable=unused-import

# Project directory
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project",
)


@pytest.mark.datafiles(DATA_DIR)
def test_default_logging(cli, tmpdir, datafiles):
    project = str(datafiles)
    bin_files_path = os.path.join(project, 'files', 'bin-files')
    element_path = os.path.join(project, 'elements')
    element_name = 'fetch-test-git.bst'

    # Create our repo object of the given source type with
    # the bin files, and then collect the initial ref.
    #
    repo = create_repo('git', str(tmpdir))
    ref = repo.create(bin_files_path)

    # Write out our test target
    element = {
        'kind': 'import',
        'sources': [
            repo.source_config(ref=ref)
        ]
    }
    _yaml.dump(element,
               os.path.join(element_path,
                            element_name))

    # Now try to fetch it
    result = cli.run(project=project, args=['source', 'fetch', element_name])
    result.assert_success()

    m = re.search(r"\[\d\d:\d\d:\d\d\]\[\s*\]\[.*\] SUCCESS Checking sources", result.stderr)
    assert m is not None


@pytest.mark.datafiles(DATA_DIR)
def test_custom_logging(cli, tmpdir, datafiles):
    project = str(datafiles)
    bin_files_path = os.path.join(project, 'files', 'bin-files')
    element_path = os.path.join(project, 'elements')
    element_name = 'fetch-test-git.bst'

    custom_log_format = ('%{elapsed},%{elapsed-us},%{wallclock},%{wallclock-us},'
                         '%{key},%{element},%{action},%{message}')
    user_config = {'logging': {'message-format': custom_log_format}}
    cli.configure(user_config)

    # Create our repo object of the given source type with
    # the bin files, and then collect the initial ref.
    #
    repo = create_repo('git', str(tmpdir))
    ref = repo.create(bin_files_path)

    # Write out our test target
    element = {
        'kind': 'import',
        'sources': [
            repo.source_config(ref=ref)
        ]
    }
    _yaml.dump(element,
               os.path.join(element_path,
                            element_name))

    # Now try to fetch it
    result = cli.run(project=project, args=['source', 'fetch', element_name])
    result.assert_success()

    m = re.search(r"\d\d:\d\d:\d\d,\d\d:\d\d:\d\d.\d{6},\d\d:\d\d:\d\d,\d\d:\d\d:\d\d.\d{6}\s*,.*"
                  r",SUCCESS,Checking sources", result.stderr)
    assert m is not None


@pytest.mark.datafiles(DATA_DIR)
def test_failed_build_listing(cli, datafiles):
    project = str(datafiles)
    element_names = []
    for i in range(3):
        element_name = 'testfail-{}.bst'.format(i)
        element_path = os.path.join('elements', element_name)
        element = {
            'kind': 'script',
            'config': {
                'commands': [
                    'false'
                ]
            }
        }
        _yaml.dump(element, os.path.join(project, element_path))
        element_names.append(element_name)
    result = cli.run(project=project, args=['--on-error=continue', 'build', *element_names])
    result.assert_main_error(ErrorDomain.STREAM, None)

    failure_heading_pos = re.search(r'^Failure Summary$', result.stderr, re.MULTILINE).start()
    pipeline_heading_pos = re.search(r'^Pipeline Summary$', result.stderr, re.MULTILINE).start()
    failure_summary_range = range(failure_heading_pos, pipeline_heading_pos)
    assert all(m.start() in failure_summary_range and m.end() in failure_summary_range
               for m in re.finditer(r'^\s+testfail-.\.bst.+?\s+Log file', result.stderr, re.MULTILINE))
