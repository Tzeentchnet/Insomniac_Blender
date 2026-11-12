class InsomniacError(Exception):
    """Base error for expected extension failures."""


class FormatError(InsomniacError):
    """Raised when an input file is malformed or has an unexpected layout."""


class UnsupportedFormatError(InsomniacError):
    """Raised for a recognized but unsupported format variant."""


class MissingResourceError(InsomniacError):
    """Raised when a required companion resource cannot be found."""


class AmbiguousResourceError(InsomniacError):
    """Raised when a suffix lookup matches more than one resource."""
