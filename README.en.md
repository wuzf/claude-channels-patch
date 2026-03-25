# claude-channels-patch

> Disclaimer: This project is provided for learning and research purposes only.
> Do not use it on software or services you do not own or do not have permission to analyze.

Binary patch for **Claude Code** that enables the `--channels` feature without requiring claude.ai OAuth authentication.

## What it does

Claude Code's `--channels` flag lets MCP servers push real-time messages into your session, for example, a Telegram bot plugin can forward chat messages directly to Claude. However, this feature is gated behind three checks:

| Gate | What it checks | Why it blocks |
|------|---------------|---------------|
| **Feature flag** | `tengu_harbor` via GrowthBook | Defaults to `false`; unreachable when `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` |
| **OAuth** | `accessToken` check | Requires claude.ai login; blocks API key / third-party proxy users |
| **Allowlist** | Channel allowlist ledger | Server-side approved list is empty without GrowthBook |

By default, the script first tries a whole decision-function patch that preserves the MCP capability check and forces channel registration. If that cannot be located safely, it falls back to the original equal-length byte edits.

## Requirements

- **Python 3.10+**

## Compatibility

- This script is intended for **Claude Code v2.1.80 and above**; tested on **2.1.81** and **2.1.83** on Windows, Linux, and macOS
- Uses **stable string anchors** (property names, return values, string literals) - no dependency on minified variable names
- Auto-detects binaries from the official install methods (**Native Install**, **Homebrew**, **WinGet**) and patches them all
- If your installation lives in a custom location, use `python patch.py --binary /path/to/claude`
- The patch will refuse to apply if the expected code patterns are not found

## Usage

```bash
# Apply patch (auto-detects all installed binaries)
python patch.py

# Check detected binaries and chosen patch strategy without modifying files
python patch.py --check

# Apply to a specific binary path
python patch.py --binary /path/to/claude

# Force the whole decision-function strategy
python patch.py --strategy decision

# Force the legacy byte-edit fallback
python patch.py --strategy legacy

# Revert to original
python patch.py revert
```

After patching, start Claude Code with channels:

```bash
claude --channels plugin:telegram@claude-plugins-official
```

## How it works

The Claude Code binary is a Node.js SEA (Single Executable Application) containing two copies of a bundled JS file. The script supports two patch strategies and, in `auto` mode, prefers the more semantic one first:

### 1. Decision-function patch (default)

The script looks for the channel decision function, keeps the first `claude/channel` capability check intact, and rewrites the rest of the function body so it returns `register`. This is closer to the behavior of "keep the protocol check, remove the business gates".

You can inspect what the script would do without modifying anything:

```bash
python patch.py --check
```

### 2. Legacy fallback byte patch

If the decision function cannot be located safely, the script falls back to 5 stable code-pattern edits across both bundled copies (10 edits total):

### 2.1 Feature flag default: `!1` -> `!0`

```javascript
// Before
function waH() { return l$("tengu_harbor", !1) }  // default = false

// After
function waH() { return l$("tengu_harbor", !0) }  // default = true
```

The `l$` function reads feature flags from GrowthBook (Anthropic's remote config). When `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is set, GrowthBook is unreachable and `l$` returns the default value. Changing the default from `false` to `true` enables the feature.

### 2.2 OAuth gate: `if(!` -> `if( `

```javascript
// Before - in B6f() channel registration
if (!SL()?.accessToken)
  return { action: "skip", kind: "auth", ... };

// After
if ( SL()?.accessToken)   // condition inverted, never skips
  return { action: "skip", kind: "auth", ... };
```

`SL()?.accessToken` reads the claude.ai OAuth token. API key users don't have one, so it returns `undefined`. Removing the `!` means the condition is `if(undefined)` which is falsy, the skip block never executes.

### 2.3 Allowlist check (plugin): `&&!` -> `&& `

```javascript
// Before
if (!D.dev && !KaH().some((L) => L.plugin === D.name && L.marketplace === D.marketplace))
  return { action: "skip", kind: "allowlist", ... };

// After
if (!D.dev &&  KaH().some(...))  // inverted: skips only if ON the allowlist
  return { action: "skip", kind: "allowlist", ... };
```

Removing the `!` before the allowlist lookup inverts the check: it now skips only plugins that are on the allowlist, letting non-allowlisted plugins through.

### 2.4 Allowlist check (server): `if(!` -> `if( `

```javascript
// Before
else if (!D.dev) return { action: "skip", kind: "allowlist", ... };

// After
else if ( D.dev) return { action: "skip", kind: "allowlist", ... };
```

`D.dev` is `undefined` for production plugins, so `if( D.dev)` is always falsy, the skip never executes.

### 2.5 noAuth state: `noAuth:!` -> `noAuth: `

```javascript
// Before
noAuth: !SL()?.accessToken   // true when no OAuth token

// After
noAuth:  SL()?.accessToken   // undefined (falsy) - treated as not-noAuth
```

### Why two copies?

The binary embeds the JS bundle twice (main thread and worker). Every patch is applied at both offsets to ensure consistency.

## Legacy patch summary

| # | Anchor | Byte change | Purpose |
|---|--------|-------------|---------|
| 1 | `tengu_harbor",!1)}` | `1` -> `0` | Feature flag default |
| 2 | `?.accessToken)return{action:"skip",kind:"auth"` | `!` -> ` ` | OAuth bypass |
| 3 | `.marketplace))return{action:"skip",kind:"allowlist"` | `!` -> ` ` | Plugin allowlist bypass |
| 4 | `)return{action:"skip",kind:"allowlist",reason:\`server` | `!` -> ` ` | Server allowlist bypass |
| 5 | `noAuth:!` | `!` -> ` ` | UI noAuth state |

## Safety

- **Backup**: The original binary is saved as `*.bak` before any modification
- **Pattern matching**: Locates targets dynamically - no hardcoded offsets, no silent corruption
- **Atomic write**: Uses temp file + `os.replace()` to avoid corrupting a running binary
- **Revertible**: `python patch.py revert` restores all binaries at any time

## License

MIT
