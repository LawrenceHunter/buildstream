import os
import pytest
import hashlib
import tempfile
from ruamel import yaml

from tests.testutils import generate_junction, create_element_size, create_repo
from buildstream.plugintestutils import cli
from buildstream import _yaml
from buildstream._yamlcache import YamlCache
from buildstream._project import Project
from buildstream._context import Context
from contextlib import contextmanager


def generate_project(tmpdir, ref_storage, with_junction, name="test"):
    if with_junction:
        subproject_dir = generate_project(
            tmpdir, ref_storage,
            False, name='test-subproject'
        )

    project_dir = os.path.join(tmpdir, name)
    os.makedirs(project_dir)
    # project.conf
    project_conf_path = os.path.join(project_dir, 'project.conf')
    elements_path = 'elements'
    project_conf = {
        'name': name,
        'element-path': elements_path,
        'ref-storage': ref_storage,
    }
    _yaml.dump(project_conf, project_conf_path)

    # elements
    if with_junction:
        junction_name = 'junction.bst'
        junction_dir = os.path.join(project_dir, elements_path)
        junction_path = os.path.join(project_dir, elements_path, junction_name)
        os.makedirs(junction_dir)
        generate_junction(tmpdir, subproject_dir, junction_path)
        element_depends = [{'junction': junction_name, 'filename': 'test.bst'}]
    else:
        element_depends = []

    element_name = 'test.bst'
    create_element_size(element_name, project_dir, elements_path, element_depends, 1)

    return project_dir


@contextmanager
def with_yamlcache(project_dir):
    context = Context()
    project = Project(project_dir, context)
    cache_file = YamlCache.get_cache_file(project_dir)
    with YamlCache.open(context, cache_file) as yamlcache:
        yield yamlcache, project


def modified_file(input_file, tmpdir):
    with open(input_file) as f:
        data = f.read()
    assert 'variables' not in data
    data += '\nvariables: {modified: True}\n'
    _, temppath = tempfile.mkstemp(dir=tmpdir, text=True)
    with open(temppath, 'w') as f:
        f.write(data)

    return temppath


@pytest.mark.parametrize('ref_storage', ['inline', 'project.refs'])
@pytest.mark.parametrize('with_junction', [True, False], ids=['junction', 'no-junction'])
def test_yamlcache_used(cli, tmpdir, ref_storage, with_junction):
    # Generate the project
    project = generate_project(str(tmpdir), ref_storage, with_junction)
    element_path = os.path.join(project, 'elements', 'test.bst')
    element_mtime = 0
    if with_junction:
        result = cli.run(project=project, args=['source', 'fetch', '--track', 'junction.bst'])
        result.assert_success()

    # bst show to put it in the cache
    result = cli.run(project=project, args=['show', 'test.bst'])
    result.assert_success()

    with with_yamlcache(project) as (yc, prj):
        # Check that it's in the cache
        assert yc.is_cached(prj, element_path)

        # Modify files in the yaml cache to test whether it's being used
        temppath = modified_file(element_path, str(tmpdir))
        contents = _yaml.load(temppath, copy_tree=False, project=prj)
        key = yc._calculate_key(prj, element_path, copy_tree=False)
        yc.put_from_key(prj, element_path, key, contents)

    # Show that a variable has been added
    result = cli.run(project=project, args=['show', '--deps', 'none', '--format', '%{vars}', 'test.bst'])
    result.assert_success()
    data = yaml.safe_load(result.output)
    assert 'modified' in data
    assert data['modified'] == 'True'


@pytest.mark.parametrize('ref_storage', ['inline', 'project.refs'])
@pytest.mark.parametrize('with_junction', [True, False], ids=['junction', 'no-junction'])
def test_yamlcache_changed_file(cli, tmpdir, ref_storage, with_junction):
    # i.e. a file is cached, the file is changed, loading the file (with cache) returns new data
    # inline and junction can only be changed by opening a workspace
    # Generate the project
    project = generate_project(str(tmpdir), ref_storage, with_junction)
    if with_junction:
        result = cli.run(project=project, args=['source', 'fetch', '--track', 'junction.bst'])
        result.assert_success()

    # bst show to put it in the cache
    result = cli.run(project=project, args=['show', 'test.bst'])
    result.assert_success()

    element_path = os.path.join(project, 'elements', 'test.bst')
    with with_yamlcache(project) as (yc, prj):
        # Check that it's in the cache then modify
        assert yc.is_cached(prj, element_path)
        with open(element_path, "a") as f:
            f.write('\nvariables: {modified: True}\n')
        # Load modified yaml cache file into cache
        _yaml.load(element_path, copy_tree=False, project=prj, yaml_cache=yc)

    # Show that a variable has been added
    result = cli.run(project=project, args=['show', '--deps', 'none', '--format', '%{vars}', 'test.bst'])
    result.assert_success()
    data = yaml.safe_load(result.output)
    assert 'modified' in data
    assert data['modified'] == 'True'
