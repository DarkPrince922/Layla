"""Точечная правка и дозапись файлов агентом: без переписывания файла целиком."""
from __future__ import annotations

from app.services import files, project_agent


def _write(root, path, text):
    return project_agent.execute(str(root), "write_file", {"path": path, "content": text, "expected_sha256": None})


def test_edit_replaces_only_the_fragment(tmp_path):
    _write(tmp_path, "index.html", "<button class='red'>Купить</button>\n<p>цена</p>\n")
    result = project_agent.execute(str(tmp_path), "edit_file", {
        "path": "index.html", "old_string": "class='red'", "new_string": "class='blue'"})
    assert result["change"]["operation"] == "edit"
    assert files.read_text(tmp_path, "index.html") == "<button class='blue'>Купить</button>\n<p>цена</p>\n"


def test_edit_requires_unique_or_replace_all(tmp_path):
    _write(tmp_path, "a.css", ".a{color:red}\n.b{color:red}\n")
    ambiguous = project_agent.execute(str(tmp_path), "edit_file", {
        "path": "a.css", "old_string": "color:red", "new_string": "color:blue"})
    assert "встречается 2 раз" in ambiguous["error"]
    assert files.read_text(tmp_path, "a.css") == ".a{color:red}\n.b{color:red}\n"
    project_agent.execute(str(tmp_path), "edit_file", {
        "path": "a.css", "old_string": "color:red", "new_string": "color:blue", "replace_all": True})
    assert files.read_text(tmp_path, "a.css") == ".a{color:blue}\n.b{color:blue}\n"


def test_edit_reports_missing_fragment_and_stale_version(tmp_path):
    _write(tmp_path, "a.txt", "hello")
    missing = project_agent.execute(str(tmp_path), "edit_file", {
        "path": "a.txt", "old_string": "bye", "new_string": "x"})
    assert "не найден" in missing["error"]
    stale = project_agent.execute(str(tmp_path), "edit_file", {
        "path": "a.txt", "old_string": "hello", "new_string": "x", "expected_sha256": "0" * 64})
    assert stale["code"] == "conflict" and files.read_text(tmp_path, "a.txt") == "hello"
    absent = project_agent.execute(str(tmp_path), "edit_file", {
        "path": "nope.txt", "old_string": "a", "new_string": "b"})
    assert "не найден" in absent["error"]


def test_append_writes_large_file_in_parts(tmp_path):
    _write(tmp_path, "index.html", "<html><body>\n")
    for part in ("<section>1</section>\n", "<section>2</section>\n", "</body></html>\n"):
        result = project_agent.execute(str(tmp_path), "append_file", {"path": "index.html", "content": part})
        assert result["change"]["operation"] == "edit"
    assert files.read_text(tmp_path, "index.html") == (
        "<html><body>\n<section>1</section>\n<section>2</section>\n</body></html>\n")
    assert "не найден" in project_agent.execute(str(tmp_path), "append_file", {
        "path": "new.html", "content": "x"})["error"]


def test_preview_shows_edit_diff_without_writing(tmp_path):
    _write(tmp_path, "a.txt", "one\ntwo\n")
    change = project_agent.preview(str(tmp_path), "edit_file", {
        "path": "a.txt", "old_string": "two", "new_string": "три"})
    assert "-two" in change["diff"] and "+три" in change["diff"]
    assert files.read_text(tmp_path, "a.txt") == "one\ntwo\n"


def test_edit_tools_need_write_permission():
    names = lambda perms: {t["function"]["name"] for t in project_agent.permitted_tools(perms)}  # noqa: E731
    assert names(["files.read"]) == {"list_files", "read_file"}
    assert {"edit_file", "append_file"} <= names(["files.read", "files.write"])
    assert {"edit_file", "append_file"} <= project_agent.MUTATING  # в режиме «План» их нет
