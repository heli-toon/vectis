"""Vectis zero-dependency sandboxed tool execution and workspace management."""

import json
import os
import subprocess
import config


def get_root_ticket_id(conn, ticket_id):
    """Find the root ticket ID for a given ticket by traversing up parent tickets."""
    current_id = ticket_id
    visited = set()
    while current_id is not None:
        if current_id in visited:
            break
        visited.add(current_id)
        row = conn.execute(
            "SELECT id, parent_ticket_id FROM tickets WHERE id = ?", (current_id,)
        ).fetchone()
        if not row:
            break
        if row["parent_ticket_id"] is None:
            return row["id"]
        current_id = row["parent_ticket_id"]
    return current_id or ticket_id


def get_ticket_workspace(conn, ticket_id, create=True):
    """Return the absolute path of the workspace directory for the ticket's root ticket."""
    root_id = get_root_ticket_id(conn, ticket_id)
    base_dir = getattr(config, "WORKSPACES_DIR", "workspaces")
    workspace_path = os.path.abspath(os.path.join(base_dir, f"ticket_{root_id}"))
    if create:
        os.makedirs(workspace_path, exist_ok=True)
    return workspace_path


def resolve_workspace_path(workspace_dir, relative_path):
    """Safely resolve a relative path within workspace_dir, preventing path traversal."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("File path must be a non-empty string.")

    abs_workspace = os.path.abspath(workspace_dir)
    # Strip leading separators to ensure os.path.join treats it as relative
    clean_path = relative_path.lstrip("/\\")
    resolved_path = os.path.abspath(os.path.join(abs_workspace, clean_path))

    # Boundary check: target must be inside abs_workspace
    if not (resolved_path == abs_workspace or resolved_path.startswith(abs_workspace + os.sep)):
        raise ValueError(
            f"Path traversal detected: '{relative_path}' resolves outside workspace boundary."
        )

    # Realpath check if target or its parent exists to prevent symlink traversal
    real_workspace = os.path.realpath(abs_workspace)
    check_target = resolved_path
    while check_target and not os.path.exists(check_target):
        check_target = os.path.dirname(check_target)

    if check_target and os.path.exists(check_target):
        real_target = os.path.realpath(check_target)
        if not (real_target == real_workspace or real_target.startswith(real_workspace + os.sep)):
            raise ValueError(
                f"Symlink path traversal detected: '{relative_path}' points outside workspace boundary."
            )

    return resolved_path


def file_write(workspace_dir, path, content=""):
    """Safely write content to a file inside the workspace directory."""
    try:
        target_path = resolve_workspace_path(workspace_dir, path)
        parent_dir = os.path.dirname(target_path)
        os.makedirs(parent_dir, exist_ok=True)

        if not isinstance(content, str):
            content = str(content)

        encoded = content.encode("utf-8")
        with open(target_path, "wb") as f:
            f.write(encoded)

        return {
            "success": True,
            "path": path,
            "bytes_written": len(encoded),
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e),
        }


def file_read(workspace_dir, path, max_bytes=None):
    """Safely read content from a file inside the workspace directory."""
    if max_bytes is None:
        max_bytes = getattr(config, "MAX_FILE_READ_BYTES", 102400)

    try:
        target_path = resolve_workspace_path(workspace_dir, path)
        if not os.path.exists(target_path):
            return {
                "success": False,
                "path": path,
                "error": f"File '{path}' does not exist.",
            }
        if os.path.isdir(target_path):
            return {
                "success": False,
                "path": path,
                "error": f"'{path}' is a directory, not a file.",
            }

        file_size = os.path.getsize(target_path)
        with open(target_path, "rb") as f:
            raw_bytes = f.read(max_bytes)

        content = raw_bytes.decode("utf-8", errors="replace")
        truncated = file_size > max_bytes

        return {
            "success": True,
            "path": path,
            "content": content,
            "size": file_size,
            "truncated": truncated,
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e),
        }


def directory_list(workspace_dir, path="."):
    """Safely list entries in a directory inside the workspace directory."""
    try:
        target_path = resolve_workspace_path(workspace_dir, path)
        if not os.path.exists(target_path):
            return {
                "success": False,
                "path": path,
                "error": f"Path '{path}' does not exist.",
            }
        if not os.path.isdir(target_path):
            return {
                "success": False,
                "path": path,
                "error": f"Path '{path}' is not a directory.",
            }

        entries = []
        for entry in sorted(os.scandir(target_path), key=lambda e: e.name):
            rel_entry = os.path.relpath(entry.path, workspace_dir)
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
            entries.append({
                "name": entry.name,
                "path": rel_entry,
                "type": "directory" if is_dir else "file",
                "size": size,
            })

        return {
            "success": True,
            "path": path,
            "entries": entries,
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e),
        }


def execute_command(workspace_dir, command, timeout_sec=None, max_output_bytes=None):
    """Safely execute a shell command inside the workspace directory with timeout and output limits."""
    if timeout_sec is None:
        timeout_sec = getattr(config, "DEFAULT_COMMAND_TIMEOUT", 30)
    if max_output_bytes is None:
        max_output_bytes = getattr(config, "MAX_COMMAND_OUTPUT_BYTES", 10240)

    if not isinstance(command, str) or not command.strip():
        return {
            "success": False,
            "command": command,
            "returncode": -1,
            "error": "Command must be a non-empty string.",
            "stdout": "",
            "stderr": "",
        }

    os.makedirs(workspace_dir, exist_ok=True)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
        )

        stdout = proc.stdout[:max_output_bytes].decode("utf-8", errors="replace")
        stderr = proc.stderr[:max_output_bytes].decode("utf-8", errors="replace")
        stdout_truncated = len(proc.stdout) > max_output_bytes
        stderr_truncated = len(proc.stderr) > max_output_bytes

        return {
            "success": proc.returncode == 0,
            "command": command,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"")[:max_output_bytes].decode("utf-8", errors="replace")
        stderr = (e.stderr or b"")[:max_output_bytes].decode("utf-8", errors="replace")
        return {
            "success": False,
            "command": command,
            "returncode": -1,
            "error": f"Command timed out after {timeout_sec} seconds",
            "stdout": stdout,
            "stderr": stderr,
        }
    except Exception as e:
        return {
            "success": False,
            "command": command,
            "returncode": -1,
            "error": str(e),
            "stdout": "",
            "stderr": "",
        }


AVAILABLE_TOOLS = {
    "file_write": file_write,
    "file_read": file_read,
    "directory_list": directory_list,
    "execute_command": execute_command,
}


def dispatch_tool(workspace_dir, tool_name, args=None):
    """Dispatch a tool call to the corresponding implementation."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {
            "tool": tool_name,
            "success": False,
            "error": f"Tool arguments must be a dictionary, got {type(args).__name__}",
        }

    func = AVAILABLE_TOOLS.get(tool_name)
    if not func:
        return {
            "tool": tool_name,
            "success": False,
            "error": f"Unknown tool '{tool_name}'. Available tools: {', '.join(sorted(AVAILABLE_TOOLS.keys()))}",
        }

    try:
        res = func(workspace_dir, **args)
        if isinstance(res, dict):
            res["tool"] = tool_name
            return res
        return {"tool": tool_name, "success": True, "result": res}
    except TypeError as e:
        return {
            "tool": tool_name,
            "success": False,
            "error": f"Invalid arguments for tool '{tool_name}': {e}",
        }
    except Exception as e:
        return {
            "tool": tool_name,
            "success": False,
            "error": f"Tool execution failed: {e}",
        }


def execute_tool_calls(workspace_dir, tool_calls):
    """Execute a list of tool call dictionaries within workspace_dir."""
    results = []
    if not isinstance(tool_calls, list):
        return results

    for call in tool_calls:
        if not isinstance(call, dict):
            results.append({
                "tool": "unknown",
                "success": False,
                "error": "Tool call must be an object",
            })
            continue

        tool_name = call.get("tool")
        args = call.get("args", {})
        if not tool_name or not isinstance(tool_name, str):
            results.append({
                "tool": str(tool_name),
                "success": False,
                "error": "Missing or invalid tool name",
            })
            continue

        result = dispatch_tool(workspace_dir, tool_name, args)
        results.append(result)

    return results
