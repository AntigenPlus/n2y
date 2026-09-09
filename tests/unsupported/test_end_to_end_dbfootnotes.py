from tests.test_end_to_end import run_n2y_page


def test_dbfootnote_plugin(tmpdir):
    """
    This test exercises our database footnote. It involves a simple page with
    an inline database containing content for 3 footnotes.

    The page can be seen here:
    https://www.notion.so/Advanced-DB-Footnote-8a1a17c7ef3043f4bd9fb041ef31ba7
    """
    object_id = "8a1a17c7ef3043f4bd9fb041ef31ba7d"
    document = run_n2y_page(
        tmpdir,
        object_id,
        plugins=[
            "n2y.plugins.unsupported.dbfootnotes",
        ],
    )

    assert "some text [^1]." in document
    assert "like so [^2] and" in document
    assert "so [^3]." in document
    assert "[^1]: Inline DB content for footnote 1." in document
    assert "[^2]: Inline DB content for footnote 2." in document
    assert "[^3]: Inline DB content for footnote 3." in document
