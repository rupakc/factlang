import factlang


def test_versions() -> None:
    assert factlang.__version__ == "0.1.3"
    assert factlang.LANGUAGE_VERSION == "1.0"
