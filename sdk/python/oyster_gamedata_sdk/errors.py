"""Exception hierarchy for the GameData SDK.

Every SDK-raised exception inherits from :class:`GameDataSDKError` so a
buyer can ``except GameDataSDKError`` and catch everything cleanly.
"""

from __future__ import annotations


class GameDataSDKError(Exception):
    """Base class for all GameData SDK errors."""


class TarballNotFoundError(GameDataSDKError, FileNotFoundError):
    """The .tar.gz path does not exist or is not readable."""


class TarballStructureError(GameDataSDKError):
    """The tarball is missing a required file (video/systeminfo/...)."""


class SchemaValidationError(GameDataSDKError, ValueError):
    """A parsed payload does not match the buyer-spec v1 schema."""


class DependencyMissingError(GameDataSDKError, ImportError):
    """A feature was requested that needs an optional dep (OpenEXR, openpyxl, cv2)."""
