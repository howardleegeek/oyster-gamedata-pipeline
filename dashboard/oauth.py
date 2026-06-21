"""
OAuth/JWT configuration for dashboard server.
Exports JWT constants for use by tests and other modules.
"""

import os

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"

__all__ = ["JWT_SECRET", "JWT_ALGORITHM"]
