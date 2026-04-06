# Classic Mac Hardware MCP Server

MCP server providing FTP-based file operations and LaunchAPPL remote execution for Classic Macintosh test machines running RumpusFTP. Designed for use with [Claude Code](https://claude.ai/code) and other MCP-compatible AI tools to deploy, test, and manage software on real Classic Mac hardware.

## Quick Start

### 1. Configure your machines

```bash
mkdir -p ~/.config/classic-mac-hardware
cp machines.example.json ~/.config/classic-mac-hardware/machines.json
# Edit machines.json with your Mac IPs, FTP credentials, etc.
```

### 2. Add to your project's `.mcp.json`

```json
{
  "mcpServers": {
    "classic-mac-hardware": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/matthewdeaves/classic-mac-hardware-mcp", "classic-mac-hardware-mcp"],
      "env": {
        "MACHINES_CONFIG": "~/.config/classic-mac-hardware/machines.json"
      }
    }
  }
}
```

### 3. Use in Claude Code

The MCP tools are automatically available. Ask Claude to upload files, run binaries, or check connections on your Classic Macs.

## Available Tools

| Tool | Description |
|------|-------------|
| `list_machines` | List configured machines and their capabilities |
| `test_connection` | Test FTP and/or LaunchAPPL connectivity |
| `list_directory` | List files (accepts `/` or `:` path separators) |
| `delete_files` | Delete files or directories (optional recursive) |
| `upload_file` | Upload with auto-mkdir for parent directories |
| `download_file` | Download (defaults to `downloads/{machine}/`) |
| `execute_binary` | Run via LaunchAPPL (serialized, 120s timeout) |

## Resources

| URI | Description |
|-----|-------------|
| `mac://{machine_id}/logs/latest` | PT_Log output from a machine |

## Machine Configuration

Each machine in `machines.json` can have:

```json
{
  "machine_id": {
    "name": "Display Name",
    "platform": "mactcp | opentransport | appletalk",
    "system": "System version",
    "cpu": "Processor info",
    "ram": "RAM amount",
    "build": "standard | lowmem",
    "ftp": {
      "host": "10.0.0.1",
      "port": 21,
      "username": "mac",
      "password": "mac"
    },
    "launchappl": {
      "host": "10.0.0.1",
      "port": 1984
    },
    "notes": "Optional notes"
  }
}
```

- Passwords support environment variable expansion: `"password": "${MY_SECRET}"`
- Machines can have FTP only, LaunchAPPL only, or both
- Config hot-reloads when the file changes (no server restart needed)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MACHINES_CONFIG` | Path to machines.json | `~/.config/classic-mac-hardware/machines.json` |

## Development

```bash
# Clone and run locally
git clone https://github.com/matthewdeaves/classic-mac-hardware-mcp
cd classic-mac-hardware-mcp
cp machines.example.json machines.json  # edit with your machines
uv run classic-mac-hardware-mcp
```

## Design Notes

- **Plain FTP** (not SFTP) for RumpusFTP compatibility
- **Rate limiting** (0.5s between operations) — old Macs need breathing room
- **Passive mode** FTP with retry logic (2 retries, 2s delay)
- **Mac colon paths** internally — input accepts `/` or `:` separators
- **Serialized LaunchAPPL** execution via async lock to prevent port conflicts
- **Single operation per FTP connection** for stability

## License

MIT
