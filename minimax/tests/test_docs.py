from pathlib import Path


def test_sample_contains_real_context_sources():
    root = Path(__file__).parents[1] / "sample"
    assert (root / "specs/notes.md").is_file()
    assert (root / "tests/test_notes_filtering.md").is_file()
    assert "existing order" in (root / "specs/notes.md").read_text()


def test_spec_update_changes_source_content():
    spec = Path(__file__).parents[1] / "sample/specs/notes.md"
    initial = spec.read_text()
    updated = initial.replace("unknown kind", "unsupported kind")
    assert updated != initial
