from chronos_repro.snapshot import freeze, verify


def test_snapshot_is_content_addressed_and_verifiable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "doc.txt").write_text("stable corpus", encoding="utf-8")
    first = freeze(source, tmp_path / "snapshots", "t17", "unit test")
    second = freeze(source, tmp_path / "snapshots", "t17", "unit test")
    assert first == second
    assert verify(first)["file_count"] == 1
