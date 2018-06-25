import os
import pytest

from tests.testutils import cli, create_repo, ALL_REPO_KINDS

from buildstream import _yaml


# Project directory
TOP_DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = os.path.join(TOP_DIR, 'project')


def generate_element(output_file):
    element = {
        'kind': 'import',
        'sources': [
            {
                'kind': 'fetch_source',
                "output-text": output_file,
                "urls": ["foo:repo1", "bar:repo2"],
                "fetch-succeeds": {
                    "FOO/repo1": True,
                    "BAR/repo2": False,
                    "OOF/repo1": False,
                    "RAB/repo2": True,
                    "OFO/repo1": False,
                    "RBA/repo2": False,
                    "ooF/repo1": False,
                    "raB/repo2": False,
                }
            }
        ]
    }
    return element


def generate_project():
    project = {
        'name': 'test',
        'element-path': 'elements',
        'aliases': {
            'foo': 'FOO/',
            'bar': 'BAR/',
        },
        'mirrors': [
            {
                'location-name': 'middle-earth',
                'aliases': {
                    'foo': ['OOF/'],
                    'bar': ['RAB/'],
                },
            },
            {
                'location-name': 'arrakis',
                'aliases': {
                    'foo': ['OFO/'],
                    'bar': ['RBA/'],
                },
            },
            {
                'location-name': 'oz',
                'aliases': {
                    'foo': ['ooF/'],
                    'bar': ['raB/'],
                }
            },
        ],
        'plugins': [
            {
                'origin': 'local',
                'path': 'sources',
                'sources': {
                    'fetch_source': 0
                }
            }
        ]
    }
    return project


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize("kind", [(kind) for kind in ALL_REPO_KINDS])
def test_mirror_fetch(cli, tmpdir, datafiles, kind):
    bin_files_path = os.path.join(str(datafiles), 'files', 'bin-files', 'usr')
    dev_files_path = os.path.join(str(datafiles), 'files', 'dev-files', 'usr')
    upstream_repodir = os.path.join(str(tmpdir), 'upstream')
    mirror_repodir = os.path.join(str(tmpdir), 'mirror')
    project_dir = os.path.join(str(tmpdir), 'project')
    os.makedirs(project_dir)
    element_dir = os.path.join(project_dir, 'elements')

    # Create repo objects of the upstream and mirror
    upstream_repo = create_repo(kind, upstream_repodir)
    upstream_ref = upstream_repo.create(bin_files_path)
    mirror_repo = upstream_repo.copy(mirror_repodir)
    mirror_ref = upstream_ref
    upstream_ref = upstream_repo.create(dev_files_path)

    element = {
        'kind': 'import',
        'sources': [
            upstream_repo.source_config(ref=upstream_ref)
        ]
    }
    element_name = 'test.bst'
    element_path = os.path.join(element_dir, element_name)
    full_repo = element['sources'][0]['url']
    upstream_map, repo_name = os.path.split(full_repo)
    alias = 'foo-' + kind
    aliased_repo = alias + ':' + repo_name
    element['sources'][0]['url'] = aliased_repo
    mirror_map, _ = os.path.split(mirror_repo.repo)
    os.makedirs(element_dir)
    _yaml.dump(element, element_path)

    project = {
        'name': 'test',
        'element-path': 'elements',
        'aliases': {
            alias: upstream_map + "/"
        },
        'mirrors': [
            {
                'location-name': 'middle-earth',
                'aliases': {
                    alias: ["file://" + mirror_map + "/"],
                },
            },
        ]
    }
    project_file = os.path.join(project_dir, 'project.conf')
    _yaml.dump(project, project_file)

    # No obvious ways of checking that the mirror has been fetched
    # But at least we can be sure it succeeds
    result = cli.run(project=project_dir, args=['fetch', element_name])
    result.assert_success()


@pytest.mark.datafiles(DATA_DIR)
def test_mirror_fetch_multi(cli, tmpdir, datafiles):
    output_file = os.path.join(str(tmpdir), "output.txt")
    project_dir = str(tmpdir)
    element_dir = os.path.join(project_dir, 'elements')
    os.makedirs(element_dir, exist_ok=True)
    element_name = "test.bst"
    element_path = os.path.join(element_dir, element_name)
    element = generate_element(output_file)
    _yaml.dump(element, element_path)

    project_file = os.path.join(project_dir, 'project.conf')
    project = generate_project()
    _yaml.dump(project, project_file)

    result = cli.run(project=project_dir, args=['fetch', element_name])
    result.assert_success()
    with open(output_file) as f:
        contents = f.read()
        assert "Fetch foo:repo1 succeeded from FOO/repo1" in contents
        assert "Fetch bar:repo2 succeeded from RAB/repo2" in contents


@pytest.mark.datafiles(DATA_DIR)
def test_mirror_fetch_default_cmdline(cli, tmpdir, datafiles):
    output_file = os.path.join(str(tmpdir), "output.txt")
    project_dir = str(tmpdir)
    element_dir = os.path.join(project_dir, 'elements')
    os.makedirs(element_dir, exist_ok=True)
    element_name = "test.bst"
    element_path = os.path.join(element_dir, element_name)
    element = generate_element(output_file)
    _yaml.dump(element, element_path)

    project_file = os.path.join(project_dir, 'project.conf')
    project = generate_project()
    _yaml.dump(project, project_file)

    result = cli.run(project=project_dir, args=['--default-mirror', 'arrakis', 'fetch', element_name])
    result.assert_success()
    with open(output_file) as f:
        contents = f.read()
        print(contents)
        # Success if fetching from arrakis' mirror happened before middle-earth's
        arrakis_str = "OFO/repo1"
        arrakis_pos = contents.find(arrakis_str)
        assert arrakis_pos != -1, "'{}' wasn't found".format(arrakis_str)
        me_str = "OOF/repo1"
        me_pos = contents.find(me_str)
        assert me_pos != -1, "'{}' wasn't found".format(me_str)
        assert arrakis_pos < me_pos, "'{}' wasn't found before '{}'".format(arrakis_str, me_str)


@pytest.mark.datafiles(DATA_DIR)
def test_mirror_fetch_default_userconfig(cli, tmpdir, datafiles):
    output_file = os.path.join(str(tmpdir), "output.txt")
    project_dir = str(tmpdir)
    element_dir = os.path.join(project_dir, 'elements')
    os.makedirs(element_dir, exist_ok=True)
    element_name = "test.bst"
    element_path = os.path.join(element_dir, element_name)
    element = generate_element(output_file)
    _yaml.dump(element, element_path)

    project_file = os.path.join(project_dir, 'project.conf')
    project = generate_project()
    _yaml.dump(project, project_file)

    cli.configure({'default-mirror': 'oz'})

    result = cli.run(project=project_dir, args=['fetch', element_name])
    result.assert_success()
    with open(output_file) as f:
        contents = f.read()
        print(contents)
        # Success if fetching from Oz' mirror happened before middle-earth's
        oz_str = "ooF/repo1"
        oz_pos = contents.find(oz_str)
        assert oz_pos != -1, "'{}' wasn't found".format(oz_str)
        me_str = "OOF/repo1"
        me_pos = contents.find(me_str)
        assert me_pos != -1, "'{}' wasn't found".format(me_str)
        assert oz_pos < me_pos, "'{}' wasn't found before '{}'".format(oz_str, me_str)


@pytest.mark.datafiles(DATA_DIR)
def test_mirror_fetch_default_cmdline_overrides_config(cli, tmpdir, datafiles):
    output_file = os.path.join(str(tmpdir), "output.txt")
    project_dir = str(tmpdir)
    element_dir = os.path.join(project_dir, 'elements')
    os.makedirs(element_dir, exist_ok=True)
    element_name = "test.bst"
    element_path = os.path.join(element_dir, element_name)
    element = generate_element(output_file)
    _yaml.dump(element, element_path)

    project_file = os.path.join(project_dir, 'project.conf')
    project = generate_project()
    _yaml.dump(project, project_file)

    cli.configure({'default-mirror': 'oz'})

    result = cli.run(project=project_dir, args=['--default-mirror', 'arrakis', 'fetch', element_name])
    result.assert_success()
    with open(output_file) as f:
        contents = f.read()
        print(contents)
        # Success if fetching from arrakis' mirror happened before middle-earth's
        arrakis_str = "OFO/repo1"
        arrakis_pos = contents.find(arrakis_str)
        assert arrakis_pos != -1, "'{}' wasn't found".format(arrakis_str)
        me_str = "OOF/repo1"
        me_pos = contents.find(me_str)
        assert me_pos != -1, "'{}' wasn't found".format(me_str)
        assert arrakis_pos < me_pos, "'{}' wasn't found before '{}'".format(arrakis_str, me_str)


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize("kind", [(kind) for kind in ALL_REPO_KINDS])
def test_mirror_track_upstream_present(cli, tmpdir, datafiles, kind):
    bin_files_path = os.path.join(str(datafiles), 'files', 'bin-files', 'usr')
    dev_files_path = os.path.join(str(datafiles), 'files', 'dev-files', 'usr')
    upstream_repodir = os.path.join(str(tmpdir), 'upstream')
    mirror_repodir = os.path.join(str(tmpdir), 'mirror')
    project_dir = os.path.join(str(tmpdir), 'project')
    os.makedirs(project_dir)
    element_dir = os.path.join(project_dir, 'elements')

    # Create repo objects of the upstream and mirror
    upstream_repo = create_repo(kind, upstream_repodir)
    upstream_ref = upstream_repo.create(bin_files_path)
    mirror_repo = upstream_repo.copy(mirror_repodir)
    mirror_ref = upstream_ref
    upstream_ref = upstream_repo.create(dev_files_path)

    element = {
        'kind': 'import',
        'sources': [
            upstream_repo.source_config(ref=upstream_ref)
        ]
    }

    element['sources'][0]
    element_name = 'test.bst'
    element_path = os.path.join(element_dir, element_name)
    full_repo = element['sources'][0]['url']
    upstream_map, repo_name = os.path.split(full_repo)
    alias = 'foo-' + kind
    aliased_repo = alias + ':' + repo_name
    element['sources'][0]['url'] = aliased_repo
    mirror_map, _ = os.path.split(mirror_repo.repo)
    os.makedirs(element_dir)
    _yaml.dump(element, element_path)

    project = {
        'name': 'test',
        'element-path': 'elements',
        'aliases': {
            alias: upstream_map + "/"
        },
        'mirrors': [
            {
                'location-name': 'middle-earth',
                'aliases': {
                    alias: ["file://" + mirror_map + "/"],
                },
            },
        ]
    }
    project_file = os.path.join(project_dir, 'project.conf')
    _yaml.dump(project, project_file)

    result = cli.run(project=project_dir, args=['track', element_name])
    result.assert_success()

    # Tracking tries upstream first. Check the ref is from upstream.
    new_element = _yaml.load(element_path)
    source = new_element['sources'][0]
    if 'ref' in source:
        assert source['ref'] == upstream_ref


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.parametrize("kind", [(kind) for kind in ALL_REPO_KINDS])
def test_mirror_track_upstream_absent(cli, tmpdir, datafiles, kind):
    bin_files_path = os.path.join(str(datafiles), 'files', 'bin-files', 'usr')
    dev_files_path = os.path.join(str(datafiles), 'files', 'dev-files', 'usr')
    upstream_repodir = os.path.join(str(tmpdir), 'upstream')
    mirror_repodir = os.path.join(str(tmpdir), 'mirror')
    project_dir = os.path.join(str(tmpdir), 'project')
    os.makedirs(project_dir)
    element_dir = os.path.join(project_dir, 'elements')

    # Create repo objects of the upstream and mirror
    upstream_repo = create_repo(kind, upstream_repodir)
    upstream_ref = upstream_repo.create(bin_files_path)
    mirror_repo = upstream_repo.copy(mirror_repodir)
    mirror_ref = upstream_ref
    upstream_ref = upstream_repo.create(dev_files_path)

    element = {
        'kind': 'import',
        'sources': [
            upstream_repo.source_config(ref=upstream_ref)
        ]
    }

    element['sources'][0]
    element_name = 'test.bst'
    element_path = os.path.join(element_dir, element_name)
    full_repo = element['sources'][0]['url']
    upstream_map, repo_name = os.path.split(full_repo)
    alias = 'foo-' + kind
    aliased_repo = alias + ':' + repo_name
    element['sources'][0]['url'] = aliased_repo
    mirror_map, _ = os.path.split(mirror_repo.repo)
    os.makedirs(element_dir)
    _yaml.dump(element, element_path)

    project = {
        'name': 'test',
        'element-path': 'elements',
        'aliases': {
            alias: 'http://www.example.com/'
        },
        'mirrors': [
            {
                'location-name': 'middle-earth',
                'aliases': {
                    alias: ["file://" + mirror_map + "/"],
                },
            },
        ]
    }
    project_file = os.path.join(project_dir, 'project.conf')
    _yaml.dump(project, project_file)

    result = cli.run(project=project_dir, args=['track', element_name])
    result.assert_success()

    # Check that tracking fell back to the mirror
    new_element = _yaml.load(element_path)
    source = new_element['sources'][0]
    if 'ref' in source:
        assert source['ref'] == mirror_ref
