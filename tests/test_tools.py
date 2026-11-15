"""Tests for Vectis tools: workspace isolation, path traversal security, and zero-dependency tools."""

import os
import sys
import pytest
import config
import tools


def test_get_root_ticket_id(mem_db):
    """Verify traversal to root ticket ID."""
    # Root ticket
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('Root', 'desc', 'ceo', 'human')"
    )
    root_id = cur.lastrowid

    # Child ticket
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) VALUES ('Child', 'desc', 'dev', 'ceo', ?)",
        (root_id,),
    )
    child_id = cur.lastrowid

    # Grandchild ticket
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) VALUES ('Grandchild', 'desc', 'dev', 'dev', ?)",
        (child_id,),
    )
    grandchild_id = cur.lastrowid

    assert tools.get_root_ticket_id(mem_db, root_id) == root_id
    assert tools.get_root_ticket_id(mem_db, child_id) == root_id
    assert tools.get_root_ticket_id(mem_db, grandchild_id) == root_id
    assert tools.get_root_ticket_id(mem_db, 99999) == 99999


def test_get_ticket_workspace(mem_db, tmp_path, monkeypatch):
    """Verify workspace path creation and inheritance."""
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path / "workspaces"))

    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('Root', 'desc', 'ceo', 'human')"
    )
    root_id = cur.lastrowid

    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) VALUES ('Child', 'desc', 'dev', 'ceo', ?)",
        (root_id,),
    )
    child_id = cur.lastrowid

    ws_root = tools.get_ticket_workspace(mem_db, root_id)
    ws_child = tools.get_ticket_workspace(mem_db, child_id)

    assert ws_root == ws_child
    assert os.path.isdir(ws_root)
    assert os.path.basename(ws_root) == f"ticket_{root_id}"


def test_resolve_workspace_path_valid(tmp_path):
    """Verify resolving valid relative paths."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    path1 = tools.resolve_workspace_path(ws, "file.txt")
    assert path1 == os.path.join(ws, "file.txt")

    path2 = tools.resolve_workspace_path(ws, "subdir/nested.py")
    assert path2 == os.path.join(ws, "subdir", "nested.py")


def test_resolve_workspace_path_traversal_prevention(tmp_path):
    """Verify path traversal attacks are caught and rejected."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    # Relative path escaping parent
    with pytest.raises(ValueError, match="Path traversal detected"):
        tools.resolve_workspace_path(ws, "../outside.txt")

    with pytest.raises(ValueError, match="Path traversal detected"):
        tools.resolve_workspace_path(ws, "sub/../../outside.txt")

    # Empty path
    with pytest.raises(ValueError, match="File path must be a non-empty string"):
        tools.resolve_workspace_path(ws, "")


def test_file_write_and_read(tmp_path):
    """Verify file_write creates directories and writes content, and file_read reads it."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    # Write file in nested subdirectory
    res_w = tools.file_write(ws, "nested/code/app.py", "print('hello world')\n")
    assert res_w["success"] is True
    assert res_w["bytes_written"] > 0
    assert os.path.exists(os.path.join(ws, "nested/code/app.py"))

    # Read the file back
    res_r = tools.file_read(ws, "nested/code/app.py")
    assert res_r["success"] is True
    assert res_r["content"] == "print('hello world')\n"
    assert res_r["truncated"] is False

    # Read non-existent file
    res_missing = tools.file_read(ws, "nonexistent.txt")
    assert res_missing["success"] is False
    assert "does not exist" in res_missing["error"]

    # Read directory as file
    res_dir = tools.file_read(ws, "nested")
    assert res_dir["success"] is False
    assert "is a directory" in res_dir["error"]

    # Traversal attempt in file_write
    res_bad = tools.file_write(ws, "../escape.txt", "data")
    assert res_bad["success"] is False
    assert "Path traversal detected" in res_bad["error"]


def test_file_read_max_bytes_truncation(tmp_path):
    """Verify file_read truncates at max_bytes."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    content = "A" * 1000
    tools.file_write(ws, "large.txt", content)

    res = tools.file_read(ws, "large.txt", max_bytes=100)
    assert res["success"] is True
    assert len(res["content"]) == 100
    assert res["size"] == 1000
    assert res["truncated"] is True


def test_directory_list(tmp_path):
    """Verify directory_list reports files and subdirectories."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    tools.file_write(ws, "file1.txt", "abc")
    tools.file_write(ws, "file2.py", "xyz123")
    tools.file_write(ws, "sub/file3.json", "{}")

    res = tools.directory_list(ws, ".")
    assert res["success"] is True
    entries = {e["name"]: e for e in res["entries"]}

    assert "file1.txt" in entries
    assert entries["file1.txt"]["type"] == "file"
    assert entries["file1.txt"]["size"] == 3

    assert "file2.py" in entries
    assert entries["file2.py"]["type"] == "file"

    assert "sub" in entries
    assert entries["sub"]["type"] == "directory"

    # Non-existent directory
    res_bad = tools.directory_list(ws, "nonexistent")
    assert res_bad["success"] is False
    assert "does not exist" in res_bad["error"]


def test_execute_command_success_and_failure(tmp_path):
    """Verify execute_command captures output and handles non-zero exit codes."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    # Successful command
    res = tools.execute_command(ws, "echo 'hello from sandbox'")
    assert res["success"] is True
    assert res["returncode"] == 0
    assert "hello from sandbox" in res["stdout"]

    # Subprocess runs in workspace cwd
    tools.file_write(ws, "test.txt", "vectis workspace")
    res_cwd = tools.execute_command(ws, "cat test.txt")
    assert res_cwd["success"] is True
    assert "vectis workspace" in res_cwd["stdout"]

    # Failing command
    res_fail = tools.execute_command(ws, "python3 -c 'import sys; sys.exit(42)'")
    assert res_fail["success"] is False
    assert res_fail["returncode"] == 42


def test_execute_command_timeout(tmp_path):
    """Verify execute_command terminates cleanly when timeout is reached."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    res = tools.execute_command(ws, "sleep 3", timeout_sec=0.2)
    assert res["success"] is False
    assert res["returncode"] == -1
    assert "timed out after 0.2 seconds" in res["error"]


def test_execute_command_output_capping(tmp_path):
    """Verify execute_command caps stdout to prevent memory spikes."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    # Command outputting 20000 characters with 1000 byte cap
    cmd = "python3 -c \"print('X' * 20000)\""
    res = tools.execute_command(ws, cmd, max_output_bytes=1000)
    assert res["success"] is True
    assert len(res["stdout"]) == 1000
    assert res["stdout_truncated"] is True


def test_dispatch_tool(tmp_path):
    """Verify dispatch_tool dispatches registered tools and handles errors."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    # Valid write
    res = tools.dispatch_tool(ws, "file_write", {"path": "a.txt", "content": "hello"})
    assert res["success"] is True
    assert res["tool"] == "file_write"

    # Unknown tool
    res_unk = tools.dispatch_tool(ws, "rmdir_all", {})
    assert res_unk["success"] is False
    assert "Unknown tool" in res_unk["error"]

    # Invalid args (not a dict)
    res_bad_args = tools.dispatch_tool(ws, "file_write", "not a dict")
    assert res_bad_args["success"] is False
    assert "must be a dictionary" in res_bad_args["error"]


def test_execute_tool_calls(tmp_path):
    """Verify execute_tool_calls executes a batch of tool calls."""
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)

    calls = [
        {"tool": "file_write", "args": {"path": "math_mod.py", "content": "def add(a, b): return a + b\n"}},
        {"tool": "execute_command", "args": {"command": "python3 -c \"import math_mod; print(math_mod.add(2, 3))\""}},
    ]
    results = tools.execute_tool_calls(ws, calls)
    assert len(results) == 2
    assert results[0]["success"] is True
    assert results[1]["success"] is True
    assert "5" in results[1]["stdout"]
