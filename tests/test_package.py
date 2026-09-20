import jev_use


def test_package_exposes_version() -> None:
    assert isinstance(jev_use.__version__, str)
    assert jev_use.__version__.count(".") == 2


def test_fixtures_dir_exists(fixtures_dir) -> None:
    assert (fixtures_dir / "trees").is_dir()
    assert (fixtures_dir / "responses").is_dir()
