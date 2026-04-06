#!/usr/bin/env python3
"""
Classic Mac Hardware MCP Server

Provides FTP-based access to Classic Macintosh test machines running RumpusFTP,
and LaunchAPPL-based remote execution.

Key design decisions for RumpusFTP compatibility:
- Plain FTP (not SFTP) with passive mode
- Rate limiting between operations (old Macs are slow)
- Mac-style colon paths internally, normalize on input
- Single operation per connection for stability
"""

import asyncio
import json
import os
import sys
import time
from ftplib import FTP
from pathlib import Path
from typing import Tuple

from mcp.server.fastmcp import FastMCP

# Rate limiting for RumpusFTP stability (old Macs need time between operations)
FTP_OPERATION_DELAY = 0.5  # seconds between FTP operations
FTP_RETRY_DELAY = 2.0      # seconds before retry after failure
FTP_MAX_RETRIES = 2

mcp = FastMCP("classic-mac-hardware")

# Module-level state, initialized in main()
_server = None


def _resolve_config_path() -> str:
    """Resolve machines config path: env var > XDG > local."""
    env_path = os.environ.get("MACHINES_CONFIG")
    if env_path:
        return os.path.expanduser(env_path)
    xdg = Path.home() / ".config" / "classic-mac-hardware" / "machines.json"
    if xdg.exists():
        return str(xdg)
    if Path("machines.json").exists():
        return "machines.json"
    return str(xdg)  # default target even if missing


class ClassicMacHardware:
    """Internal helper: config, FTP operations, rate limiting, LaunchAPPL."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.machines = {}
        self._config_mtime = 0
        self._first_load = True
        self._last_ftp_time = 0
        self._exec_lock = asyncio.Lock()
        self._reload_if_changed()
        self._first_load = False

    # =========================================================================
    # Path Normalization
    # =========================================================================

    def normalize_path(self, path: str) -> str:
        """
        Normalize path to Mac colon format for FTP.

        Input formats accepted:
        - "/" or empty -> root (no cwd needed)
        - "/folder/subfolder" -> "folder:subfolder"
        - "folder:subfolder" -> unchanged
        - "folder/subfolder" -> "folder:subfolder"
        """
        if not path or path == "/" or path == ".":
            return ""
        path = path.strip("/")
        path = path.replace("/", ":")
        return path

    def split_path(self, path: str) -> Tuple[str, str]:
        """Split path into (directory, filename). Directory may be empty."""
        path = self.normalize_path(path)
        if ":" in path:
            parts = path.rsplit(":", 1)
            return (parts[0], parts[1])
        return ("", path)

    # =========================================================================
    # FTP Operations with Rate Limiting
    # =========================================================================

    def rate_limit(self):
        """Wait if needed to avoid overwhelming RumpusFTP."""
        elapsed = time.time() - self._last_ftp_time
        if elapsed < FTP_OPERATION_DELAY:
            time.sleep(FTP_OPERATION_DELAY - elapsed)
        self._last_ftp_time = time.time()

    def connect_ftp(self, machine_id: str) -> FTP:
        """Create FTP connection with rate limiting."""
        self.validate_machine_id(machine_id)
        machine = self.machines[machine_id]
        if 'ftp' not in machine:
            raise ValueError(
                f"FTP not configured for {machine['name']}. "
                "This machine uses LaunchAPPL only."
            )
        self.rate_limit()
        ftp_config = machine['ftp']
        ftp = FTP()
        ftp.set_pasv(True)
        ftp.connect(ftp_config['host'], ftp_config.get('port', 21), timeout=30)
        ftp.login(ftp_config['username'], ftp_config['password'])
        return ftp

    def ftp_operation(self, machine_id: str, operation, *args, **kwargs):
        """Execute FTP operation with retry logic."""
        last_error = None
        for attempt in range(FTP_MAX_RETRIES):
            try:
                ftp = self.connect_ftp(machine_id)
                try:
                    result = operation(ftp, *args, **kwargs)
                    return result
                finally:
                    try:
                        ftp.quit()
                    except Exception:
                        pass
            except Exception as e:
                last_error = e
                if attempt < FTP_MAX_RETRIES - 1:
                    time.sleep(FTP_RETRY_DELAY)
        raise last_error

    # =========================================================================
    # Configuration
    # =========================================================================

    def _reload_if_changed(self) -> bool:
        """Hot-reload configuration if machines.json has changed."""
        try:
            current_mtime = os.path.getmtime(self.config_path)
            if current_mtime > self._config_mtime:
                self.machines = self._load_config()
                self._config_mtime = current_mtime
                print(
                    f"Loaded config: {len(self.machines)} machines",
                    file=sys.stderr,
                )
                return True
            return False
        except FileNotFoundError:
            if self._first_load:
                print(
                    "No machines configured. Copy machines.example.json to "
                    f"{self.config_path} and edit it.",
                    file=sys.stderr,
                )
            return False
        except Exception as e:
            print(f"Config reload failed: {e}", file=sys.stderr)
            return False

    def _load_config(self) -> dict:
        """Load and validate machines configuration."""
        try:
            with open(self.config_path) as f:
                config = json.load(f)
            # Expand environment variables in passwords
            for machine_id, machine in config.items():
                if 'ftp' in machine and 'password' in machine['ftp']:
                    password = machine['ftp']['password']
                    if password.startswith('${') and password.endswith('}'):
                        env_var = password[2:-1]
                        machine['ftp']['password'] = os.environ.get(env_var, '')
            return config
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return {}

    def validate_machine_id(self, machine_id: str) -> None:
        """Validate machine ID and raise helpful error if invalid."""
        if machine_id not in self.machines:
            available = (
                ', '.join(self.machines.keys()) if self.machines else '(none)'
            )
            raise ValueError(
                f"Unknown machine: '{machine_id}'\n"
                f"Available: {available}\n"
                f"Edit {self.config_path} to add machines."
            )

    def has_ftp(self, machine_id: str) -> bool:
        """Check if machine has FTP configured."""
        self.validate_machine_id(machine_id)
        return 'ftp' in self.machines[machine_id]

    def has_launchappl(self, machine_id: str) -> bool:
        """Check if machine has LaunchAPPL configured."""
        self.validate_machine_id(machine_id)
        return 'launchappl' in self.machines[machine_id]

    def ensure_fresh(self):
        """Reload config if changed. Call at start of each tool."""
        self._reload_if_changed()


def _get() -> ClassicMacHardware:
    """Get the server instance, ensuring config is fresh."""
    global _server
    if _server is None:
        _server = ClassicMacHardware(_resolve_config_path())
    _server.ensure_fresh()
    return _server


# =============================================================================
# Tools
# =============================================================================


@mcp.tool()
def list_machines() -> str:
    """List configured Classic Mac machines with their capabilities."""
    s = _get()
    if not s.machines:
        return (
            "No machines configured.\n"
            f"Copy machines.example.json to {s.config_path} and edit it."
        )

    lines = ["Configured machines:\n"]
    for mid, m in s.machines.items():
        host = (
            m.get('ftp', {}).get('host')
            or m.get('launchappl', {}).get('host', 'unknown')
        )
        features = []
        if 'ftp' in m:
            features.append('FTP')
        if 'launchappl' in m:
            features.append('LaunchAPPL')
        features_str = '+'.join(features) if features else 'no remote'

        build_type = m.get('build', 'standard')
        ram = m.get('ram', '')
        build_info = f" [{build_type}]" if build_type == 'lowmem' else ""
        ram_info = f" ({ram})" if ram else ""

        lines.append(
            f"  {mid}: {m['name']} ({m['platform']}) - "
            f"{host} [{features_str}]{ram_info}{build_info}"
        )

    if any(m.get('build') == 'lowmem' for m in s.machines.values()):
        lines.append("")
        lines.append("Machines marked [lowmem] require *_lowmem.bin builds!")

    return "\n".join(lines)


@mcp.tool()
def test_connection(machine: str, test_launchappl: bool = False) -> str:
    """Test FTP and/or LaunchAPPL connectivity to a machine."""
    s = _get()
    s.validate_machine_id(machine)
    m = s.machines[machine]

    results = []

    # Test FTP only if configured
    if 'ftp' in m:
        try:
            ftp = s.connect_ftp(machine)
            pwd = ftp.pwd()
            ftp.quit()
            results.append(f"FTP: Connected (root: {pwd})")
        except Exception as e:
            results.append(f"FTP: FAILED - {str(e)}")
    else:
        results.append("FTP: Not configured")

    # Test LaunchAPPL if configured or explicitly requested
    if 'launchappl' in m or test_launchappl:
        import socket
        try:
            la_config = m.get('launchappl', {})
            host = la_config.get('host') or m.get('ftp', {}).get('host')
            port = la_config.get('port', 1984)

            if not host:
                results.append("LaunchAPPL: No host configured")
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    results.append(f"LaunchAPPL: Port {port} open")
                else:
                    results.append(f"LaunchAPPL: Port {port} not responding")
        except Exception as e:
            results.append(f"LaunchAPPL: FAILED - {str(e)}")

    return f"Connection test: {m['name']}\n\n" + "\n".join(results)


@mcp.tool()
def list_directory(machine: str, path: str = "/") -> str:
    """List files in a directory on a Classic Mac. Path can use / or : separators."""
    s = _get()
    norm_path = s.normalize_path(path)

    def operation(ftp):
        if norm_path:
            ftp.cwd(norm_path)
        items = []
        ftp.retrlines('LIST', items.append)
        return items

    items = s.ftp_operation(machine, operation)
    m = s.machines[machine]
    display_path = norm_path if norm_path else "/"

    return (
        f"Directory listing: {m['name']}:{display_path}\n\n"
        + ("\n".join(items) if items else "(empty)")
    )


@mcp.tool()
def delete_files(machine: str, path: str, recursive: bool = False) -> str:
    """Delete a file or directory on a Classic Mac."""
    s = _get()
    norm_path = s.normalize_path(path)

    if not norm_path:
        return "Cannot delete root"

    deleted = []

    def delete_recursive(ftp, target):
        try:
            ftp.delete(target)
            deleted.append(target)
        except Exception:
            try:
                original = ftp.pwd()
                ftp.cwd(target)

                if recursive:
                    items = []
                    ftp.retrlines('LIST', items.append)
                    for item in items:
                        parts = item.split(None, 8)
                        if len(parts) >= 9:
                            name = parts[8]
                            if name not in ['.', '..']:
                                delete_recursive(ftp, name)

                ftp.cwd(original)
                ftp.rmd(target)
                deleted.append(f"{target}/")
            except Exception as e:
                raise ValueError(f"Cannot delete {target}: {e}")

    def operation(ftp):
        delete_recursive(ftp, norm_path)
        return True

    s.ftp_operation(machine, operation)
    m = s.machines[machine]

    return (
        f"Deleted from {m['name']}:\n"
        + "\n".join(f"  - {d}" for d in deleted)
    )


@mcp.tool()
def upload_file(machine: str, local_path: str, remote_path: str) -> str:
    """Upload a file to a Classic Mac. Creates parent directories if needed."""
    s = _get()
    s.validate_machine_id(machine)
    m = s.machines[machine]

    if not s.has_ftp(machine):
        if s.has_launchappl(machine):
            return (
                f"Error: FTP not configured for {m['name']}. "
                "This machine uses LaunchAPPL only.\n\n"
                "Use execute_binary instead to transfer and run in one step:\n"
                f'  execute_binary(machine="{machine}", platform="mactcp", '
                f'binary_path="{local_path}")'
            )
        return f"Error: No FTP or LaunchAPPL configured for {m['name']}."

    if not Path(local_path).exists():
        return f"Local file not found: {local_path}"

    directory, filename = s.split_path(remote_path)
    file_size = Path(local_path).stat().st_size

    def operation(ftp):
        if directory:
            try:
                ftp.cwd(directory)
            except Exception:
                # Create parent directories
                parts = directory.split(":")
                current = ""
                for part in parts:
                    current = f"{current}:{part}" if current else part
                    try:
                        ftp.mkd(current)
                    except Exception:
                        pass
                ftp.cwd(directory)

        with open(local_path, 'rb') as f:
            ftp.storbinary(f'STOR {filename}', f)

        return True

    s.ftp_operation(machine, operation)

    return (
        f"Uploaded to {m['name']}:\n\n"
        f"  Local:  {local_path}\n"
        f"  Remote: {remote_path}\n"
        f"  Size:   {file_size:,} bytes"
    )


@mcp.tool()
def download_file(
    machine: str, remote_path: str, local_path: str = ""
) -> str:
    """Download a file from a Classic Mac."""
    s = _get()
    directory, filename = s.split_path(remote_path)

    if not local_path:
        download_dir = Path(f"downloads/{machine}")
        download_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(download_dir / filename)

    def operation(ftp):
        if directory:
            ftp.cwd(directory)
        with open(local_path, 'wb') as f:
            ftp.retrbinary(f'RETR {filename}', f.write)
        return True

    s.ftp_operation(machine, operation)
    m = s.machines[machine]
    file_size = Path(local_path).stat().st_size

    return (
        f"Downloaded from {m['name']}:\n\n"
        f"  Remote: {remote_path}\n"
        f"  Local:  {local_path}\n"
        f"  Size:   {file_size:,} bytes"
    )


@mcp.tool()
async def execute_binary(
    machine: str, platform: str, binary_path: str
) -> str:
    """Run a binary on a Classic Mac via LaunchAPPL. Requires LaunchAPPLServer running on the Mac. Platform: mactcp, opentransport, or appletalk."""
    s = _get()
    s.validate_machine_id(machine)
    m = s.machines[machine]

    # Find LaunchAPPL binary
    launchappl = None
    candidates = [
        os.path.expanduser("~/Retro68-build/toolchain/bin/LaunchAPPL"),
        "/opt/Retro68-build/toolchain/bin/LaunchAPPL",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            launchappl = candidate
            break

    if not launchappl:
        return (
            "LaunchAPPL not found. Checked:\n"
            + "\n".join(f"  - {c}" for c in candidates)
        )

    if not binary_path or not Path(binary_path).exists():
        return f"Binary not found: {binary_path}"

    # Get host from launchappl config first, fall back to ftp host
    la_config = m.get('launchappl', {})
    machine_ip = la_config.get('host') or m.get('ftp', {}).get('host')

    if not machine_ip:
        return (
            f"No host configured for {m['name']}. "
            "Add 'launchappl.host' or 'ftp.host' to machines.json"
        )

    binary_path_resolved = str(Path(binary_path).resolve())
    binary_size = Path(binary_path_resolved).stat().st_size
    binary_name = Path(binary_path_resolved).name

    # Serialize all LaunchAPPL calls -- one at a time to avoid
    # network contention and port conflicts on the Mac side.
    async with s._exec_lock:
        try:
            cmd = [
                launchappl, "-e", "tcp", "--tcp-address", machine_ip,
                binary_path_resolved,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
            )

            timeout = 120
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_text = stdout.decode() if stdout else ""
                stderr_text = stderr.decode() if stderr else ""
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return (
                    f"Timed out after {timeout}s on {m['name']}.\n"
                    f"Binary: {binary_name} ({binary_size:,} bytes)\n\n"
                    "The app may still be running. Download logs via FTP:\n"
                    f'  download_file(machine="{machine}", '
                    f'remote_path="PT_Log")'
                )

            # Brief pause after execution for Mac to clean up
            await asyncio.sleep(2)

            if proc.returncode == 0:
                output = stdout_text.strip()
                return (
                    f"Executed on {m['name']} (exit 0):\n"
                    f"  Binary: {binary_name} ({binary_size:,} bytes)\n"
                    + (
                        f"\n{output}\n"
                        if output
                        else "\n  (no stdout — check PT_Log)\n"
                    )
                )
            else:
                return (
                    f"FAILED on {m['name']} (exit {proc.returncode}):\n"
                    f"  Binary: {binary_name} ({binary_size:,} bytes)\n\n"
                    f"{stderr_text}\n\n"
                    f"Ensure LaunchAPPLServer is running on {m['name']}"
                )
        except Exception as e:
            return f"Error: {e}"


# =============================================================================
# Resources
# =============================================================================


@mcp.resource("mac://{machine_id}/logs/latest")
def read_log(machine_id: str) -> str:
    """PT_Log output from a Classic Mac machine."""
    s = _get()

    def operation(ftp):
        for log_name in ["PT_Log", "pt_log", "PT_Log.txt"]:
            try:
                lines = []
                ftp.retrlines(f'RETR {log_name}', lines.append)
                if lines:
                    return '\n'.join(lines)
            except Exception:
                pass
        return "No PT_Log file found in FTP root"

    return s.ftp_operation(machine_id, operation)


# =============================================================================
# Entry point
# =============================================================================


def main():
    """Run the Classic Mac Hardware MCP server."""
    global _server
    _server = ClassicMacHardware(_resolve_config_path())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
