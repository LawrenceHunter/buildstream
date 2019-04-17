
.. _commands:

Commands
========
This page contains documentation for each BuildStream command,
along with their possible options and arguments. Each command can be
invoked on the command line, where, in most cases, this will be from the
project's main directory.


Top-level commands
------------------

.. The bst options e.g. bst --version, or bst --verbose etc.
.. _invoking_bst:

.. click:: buildstream2._frontend:cli
   :prog: bst

.. Further description of the command goes here

----

.. _invoking_artifact:

.. click:: buildstream2._frontend.cli:artifact
   :prog: bst artifact

----

.. the `bst init` command
.. _invoking_init:

.. click:: buildstream2._frontend.cli:init
   :prog: bst init

----

.. the `bst build` command
.. _invoking_build:

.. click:: buildstream2._frontend.cli:build
   :prog: bst build

----

.. _invoking_show:

.. click:: buildstream2._frontend.cli:show
   :prog: bst show

----

.. _invoking_shell:

.. click:: buildstream2._frontend.cli:shell
   :prog: bst shell

----

.. _invoking_source:

.. click:: buildstream2._frontend.cli:source
   :prog: bst source

----

.. _invoking_workspace:

.. click:: buildstream2._frontend.cli:workspace
   :prog: bst workspace


Artifact subcommands
--------------------

.. _invoking_artifact_checkout:

.. click:: buildstream2._frontend.cli:artifact_checkout
   :prog: bst artifact checkout

----

.. _invoking_artifact_log:

.. click:: buildstream2._frontend.cli:artifact_log
   :prog: bst artifact log

----

.. _invoking_artifact_pull:

.. click:: buildstream2._frontend.cli:artifact_pull
   :prog: bst artifact pull

----

.. _invoking_artifact_push:

.. click:: buildstream2._frontend.cli:artifact_push
   :prog: bst artifact push

----

.. _invoking_artifact_delete:

.. click:: buildstream2._frontend.cli:artifact_delete
   :prog: bst artifact delete


Source subcommands
------------------

.. _invoking_source_fetch:

.. click:: buildstream2._frontend.cli:source_fetch
   :prog: bst source fetch

----

.. _invoking_source_track:

.. click:: buildstream2._frontend.cli:source_track
   :prog: bst source track

----

.. _invoking_source_checkout:

.. click:: buildstream2._frontend.cli:source_checkout
   :prog: bst source checkout


Workspace subcommands
---------------------

.. _invoking_workspace_open:

.. click:: buildstream2._frontend.cli:workspace_open
   :prog: bst workspace open

----

.. _invoking_workspace_close:

.. click:: buildstream2._frontend.cli:workspace_close
   :prog: bst workspace close

----

.. _invoking_workspace_reset:

.. click:: buildstream2._frontend.cli:workspace_reset
   :prog: bst workspace reset

----

.. _invoking_workspace_list:

.. click:: buildstream2._frontend.cli:workspace_list
   :prog: bst workspace list
