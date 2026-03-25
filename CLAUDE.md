# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Binary patch for Claude Code that enables the `--channels` feature without requiring claude.ai OAuth authentication. Patches three gates: feature flag (`tengu_harbor`), OAuth check (`accessToken`), and channel allowlist.

## Architecture

Python patch script (`patch.py`) that:

1. **Auto-detects** all installed Claude Code binaries (native `.exe` + npm versions)
2. **Finds stable anchors** — string literals and property names that don't change between builds (e.g., `.marketplace))return{action:"skip",kind:"allowlist"`, `tengu_harbor",!1)}`)
3. **Searches backwards** from anchors to locate the exact byte to change (always `!` -> space or `1` -> `0`)
4. **Single-byte replacements** — every edit changes exactly 1 byte, keeping binary size unchanged

The Claude Code binary is a Node.js SEA with **two copies** of the JS bundle, so every patch must be applied twice.

## Key Design Constraints

- **No minified name dependency**: Variable names like `SL`, `D`, `OaH` change between builds. All anchors use only stable strings (return values, property names, string literals).
- **Equal-length replacement**: All patches are 1-byte changes. No shifting, no size change.
- **Backup-first**: Always patches from the `.bak` copy, never from an already-patched binary.
- **Cross-platform**: Pure Python 3.10+, ASCII-only output, handles Windows/Linux/macOS path differences.

## Testing

```bash
# Test against a copy of the original binary
python3 patch.py --binary /path/to/claude_copy
```

## Adding New Patches

When adding a patch, follow the existing pattern:
1. Pick a **stable anchor** (string literal, property name — never a minified identifier)
2. Use `find_backwards()` / `find_all()` to locate the target byte
3. Verify the byte before replacing
4. Expect **2x matches** per anchor (or 4x if both plugin and server checks share the same anchor)
