import os
import pytest

from tests.testutils import cli

from buildstream import utils, _yaml
from buildstream._exceptions import ErrorDomain, LoadErrorReason

# Project directory
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    'project',
)


def generate_remote_import_element(input_path, output_path):
    return {
        'kind': 'import',
        'sources': [
            {
                'kind': 'remote',
                'url': 'file://{}'.format(input_path),
                'filename': output_path,
                'ref': utils.sha256sum(input_path),
            }
        ]
    }


@pytest.mark.datafiles(DATA_DIR)
def test_source_checkout(datafiles, cli):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    checkout = os.path.join(cli.directory, 'source-checkout')
    target = 'checkout-deps.bst'

    result = cli.run(project=project, args=['source-checkout', target, '--deps', 'none', checkout])
    result.assert_success()

    assert os.path.exists(os.path.join(checkout, 'checkout-deps', 'etc', 'buildstream', 'config'))


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize('deps', [('build'), ('none'), ('run'), ('all')])
def test_source_checkout_deps(datafiles, cli, deps):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    checkout = os.path.join(cli.directory, 'source-checkout')
    target = 'checkout-deps.bst'

    result = cli.run(project=project, args=['source-checkout', target, '--deps', deps, checkout])
    result.assert_success()

    # Sources of the target
    if deps == 'build':
        assert not os.path.exists(os.path.join(checkout, 'checkout-deps'))
    else:
        assert os.path.exists(os.path.join(checkout, 'checkout-deps', 'etc', 'buildstream', 'config'))

    # Sources of the target's build dependencies
    if deps in ('build', 'all'):
        assert os.path.exists(os.path.join(checkout, 'import-dev', 'usr', 'include', 'pony.h'))
    else:
        assert not os.path.exists(os.path.join(checkout, 'import-dev'))

    # Sources of the target's runtime dependencies
    if deps in ('run', 'all'):
        assert os.path.exists(os.path.join(checkout, 'import-bin', 'usr', 'bin', 'hello'))
    else:
        assert not os.path.exists(os.path.join(checkout, 'import-bin'))


@pytest.mark.datafiles(DATA_DIR)
def test_source_checkout_except(datafiles, cli):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    checkout = os.path.join(cli.directory, 'source-checkout')
    target = 'checkout-deps.bst'

    result = cli.run(project=project, args=['source-checkout', target,
                                            '--deps', 'all',
                                            '--except', 'import-bin.bst',
                                            checkout])
    result.assert_success()

    # Sources for the target should be present
    assert os.path.exists(os.path.join(checkout, 'checkout-deps', 'etc', 'buildstream', 'config'))

    # Sources for import-bin.bst should not be present
    assert not os.path.exists(os.path.join(checkout, 'import-bin'))

    # Sources for other dependencies should be present
    assert os.path.exists(os.path.join(checkout, 'import-dev', 'usr', 'include', 'pony.h'))


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize('no_fetch', [(False), (True)])
def test_source_checkout_fetch(datafiles, cli, no_fetch):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    checkout = os.path.join(cli.directory, 'source-checkout')
    target = 'remote-import-dev.bst'
    target_path = os.path.join(project, 'elements', target)

    # Create an element with remote source
    element = generate_remote_import_element(
        os.path.join(project, 'files', 'dev-files', 'usr', 'include', 'pony.h'),
        'pony.h')
    _yaml.dump(element, target_path)

    # Testing --no-fetch option requires that we do not have the sources
    # cached already
    assert cli.get_element_state(project, target) == 'fetch needed'

    args = ['source-checkout']
    if no_fetch:
        args += ['--no-fetch']
    args += [target, checkout]
    result = cli.run(project=project, args=args)

    if no_fetch:
        result.assert_main_error(ErrorDomain.PIPELINE, 'uncached-sources')
    else:
        result.assert_success()
        assert os.path.exists(os.path.join(checkout, 'remote-import-dev', 'pony.h'))
