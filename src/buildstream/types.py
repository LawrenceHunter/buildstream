#
#  Copyright (C) 2018 Bloomberg LP
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library. If not, see <http://www.gnu.org/licenses/>.
#
#  Authors:
#        Tristan Van Berkom <tristan.vanberkom@codethink.co.uk>
#        Jim MacArthur <jim.macarthur@codethink.co.uk>
#        Benjamin Schubert <bschubert15@bloomberg.net>

"""
Foundation types
================

"""

from ._types import MetaFastEnum


class FastEnum(metaclass=MetaFastEnum):
    """
    A reimplementation of a subset of the `Enum` functionality, which is far quicker than `Enum`.

    :class:`enum.Enum` attributes accesses can be really slow, and slow down the execution noticeably.
    This reimplementation doesn't suffer the same problems, but also does not reimplement everything.
    """

    name = None
    """The name of the current Enum entry, same as :func:`enum.Enum.name`
    """

    value = None
    """The value of the current Enum entry, same as :func:`enum.Enum.value`
    """

    _value_to_entry = dict()  # A dict of all values mapping to the entries in the enum

    @classmethod
    def values(cls):
        """Get all the possible values for the enum.

        Returns:
            list: the list of all possible values for the enum
        """
        return cls._value_to_entry.keys()

    def __new__(cls, value):
        try:
            return cls._value_to_entry[value]
        except KeyError:
            if type(value) is cls:  # pylint: disable=unidiomatic-typecheck
                return value
            raise ValueError("Unknown enum value: {}".format(value))

    def __eq__(self, other):
        if self.__class__ is not other.__class__:
            raise ValueError("Unexpected comparison between {} and {}".format(self, repr(other)))
        # Enums instances are unique, so creating an instance with the same value as another will just
        # send back the other one, hence we can use an identity comparison, which is much faster than '=='
        return self is other

    def __ne__(self, other):
        if self.__class__ is not other.__class__:
            raise ValueError("Unexpected comparison between {} and {}".format(self, repr(other)))
        return self is not other

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return "{}.{}".format(self.__class__.__name__, self.name)

    def __reduce__(self):
        return self.__class__, (self.value,)


class Scope(FastEnum):
    """Defines the scope of dependencies to include for a given element
    when iterating over the dependency graph in APIs like
    :func:`Element.dependencies() <buildstream.element.Element.dependencies>`
    """

    ALL = 1
    """All elements which the given element depends on, following
    all elements required for building. Including the element itself.
    """

    BUILD = 2
    """All elements required for building the element, including their
    respective run dependencies. Not including the given element itself.
    """

    RUN = 3
    """All elements required for running the element. Including the element
    itself.
    """

    NONE = 4
    """Just the element itself, no dependencies.

    *Since: 1.4*
    """


class Consistency(FastEnum):
    """Defines the various consistency states of a :class:`.Source`.
    """

    INCONSISTENT = 0
    """Inconsistent

    Inconsistent sources have no explicit reference set. They cannot
    produce a cache key, be fetched or staged. They can only be tracked.
    """

    RESOLVED = 1
    """Resolved

    Resolved sources have a reference and can produce a cache key and
    be fetched, however they cannot be staged.
    """

    CACHED = 2
    """Cached

    Sources have a cached unstaged copy in the source directory.
    """

    def __ge__(self, other):
        if self.__class__ is not other.__class__:
            raise ValueError("Unexpected comparison between {} and {}".format(self, repr(other)))
        return self.value >= other.value

    def __lt__(self, other):
        if self.__class__ is not other.__class__:
            raise ValueError("Unexpected comparison between {} and {}".format(self, repr(other)))
        return self.value < other.value


class CoreWarnings():
    """CoreWarnings()

    Some common warnings which are raised by core functionalities within BuildStream are found in this class.
    """

    OVERLAPS = "overlaps"
    """
    This warning will be produced when buildstream detects an overlap on an element
        which is not whitelisted. See :ref:`Overlap Whitelist <public_overlap_whitelist>`
    """

    REF_NOT_IN_TRACK = "ref-not-in-track"
    """
    This warning will be produced when a source is configured with a reference
    which is found to be invalid based on the configured track
    """

    BAD_ELEMENT_SUFFIX = "bad-element-suffix"
    """
    This warning will be produced when an element whose name does not end in .bst
    is referenced either on the command line or by another element
    """

    BAD_CHARACTERS_IN_NAME = "bad-characters-in-name"
    """
    This warning will be produces when filename for a target contains invalid
    characters in its name.
    """


# _KeyStrength():
#
# Strength of cache key
#
class _KeyStrength(FastEnum):

    # Includes strong cache keys of all build dependencies and their
    # runtime dependencies.
    STRONG = 1

    # Includes names of direct build dependencies but does not include
    # cache keys of dependencies.
    WEAK = 2


# _SchedulerErrorAction()
#
# Actions the scheduler can take on error
#
class _SchedulerErrorAction(FastEnum):

    # Continue building the rest of the tree
    CONTINUE = "continue"

    # finish ongoing work and quit
    QUIT = "quit"

    # Abort immediately
    TERMINATE = "terminate"


# _CacheBuildTrees()
#
# When to cache build trees
#
class _CacheBuildTrees(FastEnum):

    # Always store build trees
    ALWAYS = "always"

    # Store build trees when they might be useful for BuildStream
    # (eg: on error, to allow for a shell to debug that)
    AUTO = "auto"

    # Never cache build trees
    NEVER = "never"