"""Smoke test for telix package."""


def test_import():
    """Verify telix package can be imported."""
    import telix

    assert telix.__version__ == "0.1.0"
