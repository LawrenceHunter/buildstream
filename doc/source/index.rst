.. BuildStream documentation master file, created by
   sphinx-quickstart on Mon Nov  7 21:03:37 2016.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.


BuildStream Documentation
=========================

About BuildStream
-----------------
BuildStream is a flexible and extensible framework for the modelling of build
pipelines in a declarative YAML format, written in python.

These pipelines are composed of abstract elements which perform mutations on
*filesystem data* as input and output, and are related to eachother by their
dependencies.


.. toctree::
   :maxdepth: 2
   :caption: Installing

   index/install
   index/docker


.. toctree::
   :maxdepth: 2
   :caption: General documentation

   index/using
   index/authoring
   index/artifacts


.. toctree::
   :maxdepth: 2
   :caption: Reference documentation

   index/core
   modules

.. toctree::
   :maxdepth: 2
   :caption: Contributing

   index/resources
   index/HACKING
