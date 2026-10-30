"""Where Omnispindle keeps a user's todos.

Omnispindle stores each user's data in its own database, ``user_<email>``
(non-alphanumerics become ``_``, lowercased; see Omnispindle
``database.sanitize_database_name``). ``swarmonomicon`` is only the shared
fallback for callers with no identity, so reading it finds stale or no todos.

The identity comes from ``MCP_USER_EMAIL``, the same variable the local
Omnispindle MCP configs use. ``OMNISPINDLE_DB_NAME`` overrides it outright.
"""

import os
import re


def user_db_name_for_email(email: str) -> str:
    return "user_" + re.sub(r"[^a-zA-Z0-9_]", "_", email).lower()


def omnispindle_user_db_name() -> str | None:
    """The caller's Omnispindle database, or None when no identity is configured.

    Deliberately never falls back to the shared database: an empty answer
    from the wrong database looks exactly like "no todos".
    """
    explicit = os.getenv("OMNISPINDLE_DB_NAME")
    if explicit:
        return explicit
    email = os.getenv("MCP_USER_EMAIL")
    if email:
        return user_db_name_for_email(email)
    return None
