"""Sales dashboard main module."""


def get_dashboard() -> dict[str, str]:
    """Return the current dashboard status.

    Returns:
        A dictionary with a 'status' key indicating the dashboard health.
    """
    return {"status": "ok"}