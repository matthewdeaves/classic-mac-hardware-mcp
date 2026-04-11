# Classic Mac Hardware MCP Server

**CRITICAL: All file operations with Classic Mac hardware MUST use this MCP server's tools.**

## Required: MCP Tools

| Operation | Tool |
|-----------|------|
| List machines | `list_machines` |
| Test connection | `test_connection` |
| List directory | `list_directory` |
| Delete files | `delete_files` |
| Upload file | `upload_file` |
| Download file | `download_file` |
| Execute binary (single) | `execute_binary` |
| Execute binary (parallel) | `execute_binary_batch` |

## Prohibited: Direct FTP/Scripts

**NEVER use these for Classic Mac file operations:**

- Python ftplib scripts
- Bash `ftp` or `lftp` commands
- `curl ftp://` commands
- Manual TCP socket connections
- Any hand-written FTP implementation

## Why MCP Only?

1. **Rate limiting** — RumpusFTP on old Macs needs delays between operations
2. **Path normalization** — Converts paths to Mac colon notation automatically
3. **Error handling** — Retry logic and informative errors
4. **Consistency** — Same interface regardless of machine

## Configuration

Machines are configured in `~/.config/classic-mac-hardware/machines.json` (or override with `MACHINES_CONFIG` env var). Copy `machines.example.json` to get started. The config hot-reloads when changed.

## LaunchAPPL Execution

**Run only ONE test at a time per machine via LaunchAPPL.** Test apps bind to network ports (7353 discovery, 7354 TCP). Running multiple tests on the same machine causes port conflicts and resource leaks. Execution is serialized per-machine automatically via async lock.

**To run on multiple machines in parallel, use `execute_binary_batch`** with a list of machine IDs. This executes on all machines simultaneously in a single tool call. Default timeout: 45 seconds per machine.

## Log Collection

Test apps write logs to a `PT_Log` file on the Mac. After execution:
- **FTP machines**: Use `download_file` to retrieve PT_Log
- **LaunchAPPL-only**: Test output is captured in `execute_binary` stdout

## Deployment Methods

Machines may support FTP, LaunchAPPL, or both:
- **FTP machines**: Use `upload_file` to deploy, then run manually or via LaunchAPPL
- **LaunchAPPL-only**: Use `execute_binary` which transfers and runs in one step

## If MCP Doesn't Work

1. Check connectivity: `test_connection`
2. Restart the MCP server
3. Fix the MCP server — DO NOT fall back to raw FTP
