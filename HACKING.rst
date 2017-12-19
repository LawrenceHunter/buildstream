Hacking on BuildStream
======================
Some tips and guidelines for developers hacking on BuildStream


Getting Started
---------------
After cloning the BuildStream module with git, you will want a development installation.

First install it with Meson as normal into a location of your choice, for
example /opt/buildstream.

  sudo mkdir /opt/buildstream
  sudo chown $USER:$USER /opt/buildstream
  mkdir build
  meson .. --prefix=/opt/buildstream -Dbash_completion=no
  ninja install

You should now be able to run ``/opt/buildstream/bin/bst``. You will probably
want to add ``/opt/buildstream/bin`` to your PATH as part of your shell's
startup file.

Feel free to enable the bash_completion option, but you will need to run
`ninja install` as root in that case.

Every time you make a change to the BuildStream source code you will need
to rerun `ninja install`. This can be avoided by replacing the installed
module with a symlink to your source directory (similar to the `pip -e`
feature of Pip). Here is an example command to do this. The exact paths
will depend on your OS and Python version. Run it from the top of your
buildstream.git checkout.

  rm -R /opt/buildstream/lib64/python3.6/site-packages/buildstream
  ln -s `pwd`/buildstream /opt/buildstream/lib64/python3.6/site-packages/buildstream


Coding Style
------------
Coding style details for BuildStream


Style Guide
~~~~~~~~~~~
Python coding style for BuildStream is pep8, which is documented here: https://www.python.org/dev/peps/pep-0008/

We have a couple of minor exceptions to this standard, we dont want to compromise
code readability by being overly restrictive on line length for instance.

The pep8 linter will run automatically when running the test suite.


Imports
~~~~~~~
Module imports inside BuildStream are done with . notation

Good::

  from .context import Context

Bad::

  from buildstream.context import Context

The exception to the above rule is when authoring plugins,
plugins do not reside in the same namespace so they must
address buildstream in the imports.

An element plugin will derive from Element by importing::

  from buildstream import Element

When importing utilities specifically, dont import function names
from there, instead::

  from . import utils

This makes things clear when reading code that said functions
are not defined in the same file but come from utils.py for example.


One Class One Module
~~~~~~~~~~~~~~~~~~~~
BuildStream is mostly Object Oriented with a few utility files.

* Every object should go into its own file (module) inside the buildstream package
* Files should be named after their class in lower case with no underscore.

This is to say a class named FooBar will certainly reside in a file named foobar.py.
Unless FooBar is private in which case the file is of course _foobar.py.

When adding a public class, it should be imported in toplevel __init__.py
so that buildstream consumers can import it directly from the buildstream
package, without requiring knowledge of the BuildStream package structure,
which is allowed to change over time.


Private API
~~~~~~~~~~~
BuildStream strives to guarantee a minimal and comprehensive public API
surface both for embedders of the BuildStream pipeline and implementors
of custom plugin Elements and Sources.

Python does not have a real concept of private API, but as a convention
anything which is private uses an underscore prefix.

* Modules which are private have their file named _module.py
* Private classes are named _Class
* Private methods, class variables and instance variables have a leading underscore as well

Exceptions to the above rules is to follow a principle of least underscores:

* If a module is entirely private, there is no need for the classes
  it contains to have a leading underscore.
* If a class is entirely private, there is no need to mark its members
  as private with leading underscores.


Documenting BuildStream
-----------------------
BuildStream starts out as a documented project from day one and uses
sphinx to document itself.

Useful links:

* Sphinx documentation: http://www.sphinx-doc.org/en/1.4.8/contents.html
* rst primer: http://www.sphinx-doc.org/en/stable/rest.html


Building Docs
~~~~~~~~~~~~~
The documentation build is not integrated into the ``setup.py`` and is
difficult (or impossible) to do so, so there is a little bit of setup
you need to take care of first.

Before you can build the BuildStream documentation yourself, you need
to first install ``sphinx`` and ``sphinx-click``, using pip or some
other mechanism::

  pip install --user sphinx
  pip install --user sphinx-click

Furthermore, the documentation build requires that BuildStream itself
be installed first, this can be a developer installation as described
at the top of this text::

  cd buildstream
  pip install --user -e .

Finally, to build the current set of docs, just run the following::

  cd doc
  make

This will give you a build/html directory with the html docs.


Documenting Conventions
~~~~~~~~~~~~~~~~~~~~~~~
When adding a new class to the buildstream core, an entry referring to
the new module where the new class is defined should be added to
the toplevel index manually in doc/source/index.rst.

We use the sphinx.ext.napoleon extension for the purpose of having
a bit nicer docstrings than the default sphinx docstrings.

A docstring for a method, class or function should have the following
format::

  """Brief description of entity

  Args:
     argument1 (type): Description of arg
     argument2 (type): Description of arg

  Returns:
     Description of returned thing indicating its type

  Raises:
     SomeError, SomeOtherError

  A detailed description can go here if one is needed, only
  after the above part documents the calling conventions.
  """


Testing BuildStream
-------------------
BuildStream uses pytest for regression tests and testing out
the behavior of newly added components.

The elaborate documentation for pytest can be found here: http://doc.pytest.org/en/latest/contents.html

Don't get lost in the docs if you don't need to, follow existing examples instead.


Running Tests
~~~~~~~~~~~~~
To run the tests, just type this in the build directory::

  meson test

When debugging a test, it can be desirable to see the stdout
and stderr generated by a test, to do this use the --verbose
option:

  meson test --verbose

In order to pass in extra arguments to Pytest, set the PYTEST_ARGS
environment variable. For example, to abort on the first failure
you can run::

  PYTEST_ADDOPTS=-x meson test --verbose

If you want to run a specific test or a group of tests, you
can specify a prefix to match. E.g. if you want to run all of
the frontend tests you can do::

  PYTEST_ADDOPTS='-k tests/frontend' meson test --verbose


Adding Tests
~~~~~~~~~~~~
Tests are found in the tests subdirectory, inside of which
there is a separarate directory for each *domain* of tests.
The list of tests in maintained in ``tests/meson.build``.

If the new test is not appropriate for the existing test domains,
then simply create a new directory for it under the tests subdirectory.

Various tests may include data files to test on, there are examples
of this in the existing tests. When adding data for a test, create
a subdirectory beside your test in which to store data.

When creating a test that needs data, use the datafiles extension
to decorate your test case (again, examples exist in the existing
tests for this), documentation on the datafiles extension can
be found here: https://pypi.python.org/pypi/pytest-datafiles
