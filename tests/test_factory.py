"""Tests for the plugin Registry. Uses local instances to avoid polluting the real ones."""

from __future__ import annotations

import pytest

from src.config.factory import Registry


def test_register_and_get_returns_class() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:
        pass

    assert reg.get("alpha") is Alpha


def test_register_returns_the_original_class() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:
        pass

    assert isinstance(Alpha, type)
    assert Alpha.__name__ == "Alpha"


def test_register_duplicate_name_raises() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:  # noqa: F841 - registered, kept for side effects
        pass

    with pytest.raises(ValueError, match="already registered"):
        @reg.register("alpha")
        class AlphaAgain:
            pass


def test_get_unknown_name_raises_keyerror() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:  # noqa: F841
        pass

    with pytest.raises(KeyError, match="unknown widget 'beta'"):
        reg.get("beta")


def test_get_unknown_name_lists_known_options() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:  # noqa: F841
        pass

    @reg.register("beta")
    class Beta:  # noqa: F841
        pass

    with pytest.raises(KeyError) as excinfo:
        reg.get("gamma")
    msg = str(excinfo.value)
    assert "alpha" in msg
    assert "beta" in msg


def test_names_returns_sorted_list() -> None:
    reg = Registry("widget")

    @reg.register("beta")
    class Beta:  # noqa: F841
        pass

    @reg.register("alpha")
    class Alpha:  # noqa: F841
        pass

    assert reg.names() == ["alpha", "beta"]


def test_contains_operator() -> None:
    reg = Registry("widget")

    @reg.register("alpha")
    class Alpha:  # noqa: F841
        pass

    assert "alpha" in reg
    assert "beta" not in reg
    assert 42 not in reg  # non-string keys are gracefully False


def test_len_reflects_registration_count() -> None:
    reg = Registry("widget")
    assert len(reg) == 0

    @reg.register("alpha")
    class Alpha:  # noqa: F841
        pass

    assert len(reg) == 1
