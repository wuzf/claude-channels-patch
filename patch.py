#!/usr/bin/env python3
"""
claude-channels-patch - Enable --channels on Claude Code without claude.ai OAuth.

Patch modes:
  - auto: try a whole decision-function bypass first, then fall back to legacy
    byte-level edits if the decision function cannot be located safely.
  - decision: require the whole decision-function bypass.
  - legacy: apply the original byte-level edits only.

Usage:
  python3 patch.py                            # apply patch
  python3 patch.py --check                    # analyze only, do not modify files
  python3 patch.py revert                     # restore original binary
  python3 patch.py --binary <path>            # patch a specific binary
  python3 patch.py --strategy legacy          # force legacy patch mode
"""

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

MIN_BINARY_SIZE = 10_000_000
DECISION_WINDOW = 8_000

CAPABILITY_MARKER = "claude/channel"
FEATURE_MESSAGE = "channels feature is not currently available"
REGISTER_RETURN = 'return{action:"register"}'
SKIP_RETURN = 'return{action:"skip"'


def is_claude_binary(path: Path) -> bool:
    """Return True for plausible Claude Code SEA binaries, not wrappers."""
    try:
        return path.is_file() and path.suffix.lower() in {"", ".exe"} and path.stat().st_size >= MIN_BINARY_SIZE
    except OSError:
        return False


def add_candidate(found: list[Path], seen: set[Path], candidate: Path):
    """Resolve symlinks/wrappers to the real file and dedupe candidates."""
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except OSError:
        return
    if not is_claude_binary(resolved) or resolved in seen:
        return
    seen.add(resolved)
    found.append(resolved)


def iter_path_candidates() -> list[Path]:
    """Return every claude binary visible on PATH, not just the first one."""
    names = ("claude.exe", "claude") if os.name == "nt" else ("claude",)
    candidates = []
    seen_dirs = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry.strip('"')).expanduser()
        normalized = str(directory).lower() if os.name == "nt" else str(directory)
        if normalized in seen_dirs:
            continue
        seen_dirs.add(normalized)
        for name in names:
            candidates.append(directory / name)
    return candidates


def iter_homebrew_candidates() -> list[Path]:
    candidates = []
    for prefix in (Path("/opt/homebrew"), Path("/usr/local"), Path("/home/linuxbrew/.linuxbrew")):
        candidates.append(prefix / "bin/claude")
        caskroom = prefix / "Caskroom/claude-code"
        if not caskroom.is_dir():
            continue
        for version_dir in sorted(caskroom.iterdir()):
            candidates.append(version_dir / "claude")
    return candidates


def iter_winget_candidates() -> list[Path]:
    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        local_appdata_path = Path(local_appdata)
        candidates.append(local_appdata_path / "Microsoft/WindowsApps/claude.exe")

        packages_dir = local_appdata_path / "Microsoft/WinGet/Packages"
        if packages_dir.is_dir():
            for package_dir in sorted(packages_dir.glob("Anthropic.ClaudeCode_*")):
                candidates.extend(sorted(package_dir.rglob("claude.exe")))

        for base in (
            local_appdata_path / "Programs/Anthropic/Claude Code",
            local_appdata_path / "Programs/Claude Code",
        ):
            candidates.append(base / "claude.exe")

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        for base in (
            Path(program_files) / "Anthropic/Claude Code",
            Path(program_files) / "Claude Code",
        ):
            candidates.append(base / "claude.exe")
    return candidates


def detect_binaries() -> list[Path]:
    """Auto-detect Claude Code binaries from official install methods."""
    home = Path.home()
    found = []
    seen = set()

    add_candidate(found, seen, home / ".local/bin/claude.exe")
    add_candidate(found, seen, home / ".local/bin/claude")

    versions_dir = home / ".local/share/claude/versions"
    if versions_dir.is_dir():
        for candidate in versions_dir.iterdir():
            if candidate.is_file():
                add_candidate(found, seen, candidate)
            elif candidate.is_dir():
                add_candidate(found, seen, candidate / "claude.exe")
                add_candidate(found, seen, candidate / "claude")

    for candidate in iter_path_candidates():
        add_candidate(found, seen, candidate)

    for candidate in iter_homebrew_candidates():
        add_candidate(found, seen, candidate)

    for candidate in iter_winget_candidates():
        add_candidate(found, seen, candidate)

    if not found:
        sys.exit("Could not auto-detect Claude Code binary. Use --binary <path>.")
    return found


def find_all(data: bytes, pattern: bytes) -> list[int]:
    results, start = [], 0
    while (idx := data.find(pattern, start)) != -1:
        results.append(idx)
        start = idx + 1
    return results


def find_backwards(data: bytes, anchor_offset: int, needle: bytes, max_window: int) -> int | None:
    """Search backwards from anchor_offset for needle. Returns absolute offset or None."""
    start = max(0, anchor_offset - max_window)
    window = data[start:anchor_offset]
    pos = window.rfind(needle)
    return (start + pos) if pos != -1 else None


def patch_byte(data: bytearray, offset: int, expected: int, replacement: int, desc: str):
    actual = data[offset]
    if actual != expected:
        sys.exit(f"FAIL [{desc}] @{offset}: expected 0x{expected:02x}, got 0x{actual:02x}")
    data[offset] = replacement
    print(f"  OK {desc} @{offset}")


def locate_feature_flag_sites(data: bytes) -> list[int]:
    prefix = b'tengu_harbor",!'
    sites = []
    for off in find_all(data, prefix):
        site = off + len(prefix)
        if site < len(data) and data[site] in (0x30, 0x31):
            sites.append(site)
    return sites


def locate_backwards_sites(
    data: bytes,
    anchor: bytes,
    clean_needle: bytes,
    patched_needle: bytes,
    offset_from_match: int,
    max_window: int,
) -> list[int]:
    sites = []
    for off in find_all(data, anchor):
        pos = find_backwards(data, off, clean_needle, max_window)
        if pos is None:
            pos = find_backwards(data, off, patched_needle, max_window)
        if pos is not None:
            sites.append(pos + offset_from_match)
    return sites


def locate_noauth_sites(data: bytes) -> list[int]:
    sites = []
    for off in find_all(data, b"noAuth:"):
        site = off + 7
        if site + 1 >= len(data):
            continue
        if data[site + 1] == 0x31:
            continue
        if data[site] in (0x20, 0x21):
            sites.append(site)
    return sites


def locate_policyblocked_ui_sites(data: bytes) -> list[int]:
    sites = []
    prefix = b"policyBlocked:"
    for off in find_all(data, prefix):
        site = off + len(prefix)
        tail = data[site : site + 3]
        if tail in (b"f&&", b"0&&"):
            sites.append(site)
    return sites


def classify_legacy_patch(data: bytes) -> tuple[str, list[str]]:
    checks = [
        ("feature flag", locate_feature_flag_sites(data), 0x31, 0x30, 2),
        (
            "auth bypass",
            locate_backwards_sites(
                data,
                b'?.accessToken)return{action:"skip",kind:"auth"',
                b"if(!",
                b"if( ",
                3,
                30,
            ),
            0x21,
            0x20,
            2,
        ),
        (
            "allowlist bypass (plugin)",
            locate_backwards_sites(
                data,
                b'.marketplace))return{action:"skip",kind:"allowlist"',
                b"&&!",
                b"&& ",
                2,
                80,
            ),
            0x21,
            0x20,
            2,
        ),
        (
            "allowlist bypass (server)",
            locate_backwards_sites(
                data,
                b')return{action:"skip",kind:"allowlist",reason:`server',
                b"if(!",
                b"if( ",
                3,
                30,
            ),
            0x21,
            0x20,
            2,
        ),
        ("noAuth bypass", locate_noauth_sites(data), 0x21, 0x20, 2),
    ]

    clean = 0
    patched = 0
    details = []
    for desc, sites, expected, replacement, minimum in checks:
        if len(sites) < minimum:
            details.append(f"{desc}: missing anchors ({len(sites)} found, need >= {minimum})")
            continue
        values = [data[site] for site in sites]
        if all(value == replacement for value in values):
            patched += 1
            details.append(f"{desc}: patched ({len(sites)} site(s))")
        elif all(value == expected for value in values):
            clean += 1
            details.append(f"{desc}: clean ({len(sites)} site(s))")
        else:
            details.append(f"{desc}: mixed state ({len(sites)} site(s))")

    total = len(checks)
    if patched == total:
        return "patched", details
    if clean == total:
        return "clean", details
    return "mixed", details


def classify_decision_support_patches(data: bytes) -> tuple[str, list[str]]:
    checks = [
        ("feature flag", locate_feature_flag_sites(data), 0x31, 0x30, 2, True),
        ("noAuth UI state", locate_noauth_sites(data), 0x21, 0x20, 2, True),
        ("policyBlocked UI state", locate_policyblocked_ui_sites(data), 0x66, 0x30, 2, False),
    ]

    required_clean = 0
    required_patched = 0
    required_total = 0
    details = []
    for desc, sites, expected, replacement, minimum, required in checks:
        if required:
            required_total += 1
        if len(sites) < minimum:
            label = "missing anchors" if required else "optional anchor not found"
            details.append(f"{desc}: {label} ({len(sites)} found, need >= {minimum})")
            continue
        values = [data[site] for site in sites]
        if all(value == replacement for value in values):
            if required:
                required_patched += 1
            details.append(f"{desc}: patched ({len(sites)} site(s))")
        elif all(value == expected for value in values):
            if required:
                required_clean += 1
            details.append(f"{desc}: clean ({len(sites)} site(s))")
        else:
            details.append(f"{desc}: mixed state ({len(sites)} site(s))")

    if required_patched == required_total:
        return "patched", details
    if required_clean == required_total:
        return "clean", details
    return "mixed", details


def apply_decision_support_patches(data: bytearray) -> int:
    edits = 0

    desc = "tengu_harbor default"
    offsets = locate_feature_flag_sites(data)
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x31, 0x30, desc)
        edits += 1

    desc = "channels notice noAuth UI"
    offsets = locate_noauth_sites(data)
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x21, 0x20, desc)
        edits += 1

    desc = "channels notice policyBlocked UI"
    offsets = locate_policyblocked_ui_sites(data)
    if len(offsets) >= 2:
        for site in offsets:
            patch_byte(data, site, 0x66, 0x30, desc)
            edits += 1
    else:
        print(f"  SKIP {desc} (optional, found {len(offsets)} site(s))")

    return edits


def apply_legacy_patches(data: bytearray) -> int:
    edits = 0

    desc = "tengu_harbor default"
    offsets = locate_feature_flag_sites(data)
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x31, 0x30, desc)
        edits += 1

    desc = "B6f auth bypass"
    offsets = locate_backwards_sites(
        data,
        b'?.accessToken)return{action:"skip",kind:"auth"',
        b"if(!",
        b"if( ",
        3,
        30,
    )
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x21, 0x20, desc)
        edits += 1

    desc = "allowlist bypass (plugin)"
    offsets = locate_backwards_sites(
        data,
        b'.marketplace))return{action:"skip",kind:"allowlist"',
        b"&&!",
        b"&& ",
        2,
        80,
    )
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x21, 0x20, desc)
        edits += 1

    desc = "allowlist bypass (server)"
    offsets = locate_backwards_sites(
        data,
        b')return{action:"skip",kind:"allowlist",reason:`server',
        b"if(!",
        b"if( ",
        3,
        30,
    )
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x21, 0x20, desc)
        edits += 1

    desc = "bl6 noAuth bypass"
    offsets = locate_noauth_sites(data)
    if len(offsets) < 2:
        sys.exit(f"FAIL [{desc}]: expected >=2 matches, found {len(offsets)}")
    for site in offsets:
        patch_byte(data, site, 0x21, 0x20, desc)
        edits += 1

    return edits


def find_matching_brace(text: str, open_idx: int) -> int | None:
    """Match braces while ignoring quoted strings and comments."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return None

    depth = 0
    mode = "normal"
    i = open_idx
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if mode == "normal":
            if ch == "/" and nxt == "/":
                mode = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                mode = "block_comment"
                i += 2
                continue
            if ch == "'":
                mode = "single"
            elif ch == '"':
                mode = "double"
            elif ch == "`":
                mode = "template"
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        elif mode == "single":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                mode = "normal"
        elif mode == "double":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                mode = "normal"
        elif mode == "template":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                mode = "normal"
        elif mode == "line_comment":
            if ch == "\n":
                mode = "normal"
        elif mode == "block_comment":
            if ch == "*" and nxt == "/":
                mode = "normal"
                i += 2
                continue

        i += 1
    return None


def find_smallest_enclosing_block(text: str, marker_pos: int, capability_pos: int, register_pos: int) -> tuple[int, int] | None:
    start = max(0, capability_pos - 2_500)
    candidates = []
    for open_idx in range(capability_pos, start - 1, -1):
        if text[open_idx] != "{":
            continue
        close_idx = find_matching_brace(text, open_idx)
        if close_idx is None:
            continue
        if open_idx < capability_pos < close_idx and open_idx < marker_pos < close_idx and open_idx < register_pos < close_idx:
            candidates.append((open_idx, close_idx))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[1] - pair[0])


def find_capability_check_end(text: str, body_start: int, body_end: int, capability_pos: int) -> int | None:
    if_pos = text.rfind("if(", body_start, capability_pos)
    if if_pos == -1:
        return None

    skip_pos = text.find(SKIP_RETURN, if_pos, body_end)
    if skip_pos == -1:
        return None
    if CAPABILITY_MARKER not in text[if_pos:skip_pos]:
        return None

    object_open = text.find("{", skip_pos, body_end)
    if object_open == -1:
        return None
    object_close = find_matching_brace(text, object_open)
    if object_close is None or object_close >= body_end:
        return None

    end = object_close + 1
    while end < body_end and text[end] in " ;\r\n\t":
        end += 1
    return end


def locate_decision_patches(data: bytes) -> list[tuple[int, int, int]]:
    """
    Find decision-function bodies that can be collapsed to:
      <capability check>
      return{action:"register"}

    Returns a list of tuples:
      (body_open_brace, body_close_brace, end_of_capability_check)
    """
    text = data.decode("latin-1")
    markers = []
    start = 0
    while True:
        marker_pos = text.find(FEATURE_MESSAGE, start)
        if marker_pos == -1:
            break
        markers.append(marker_pos)
        start = marker_pos + 1

    patches = []
    seen_bodies = set()
    for marker_pos in markers:
        window_start = max(0, marker_pos - DECISION_WINDOW)
        window_end = min(len(text), marker_pos + DECISION_WINDOW)

        capability_pos = text.rfind(CAPABILITY_MARKER, window_start, marker_pos)
        if capability_pos == -1:
            continue

        register_pos = text.find(REGISTER_RETURN, marker_pos, window_end)
        if register_pos == -1:
            continue

        bounds = find_smallest_enclosing_block(text, marker_pos, capability_pos, register_pos)
        if bounds is None:
            continue
        body_start, body_end = bounds
        if (body_start, body_end) in seen_bodies:
            continue

        capability_end = find_capability_check_end(text, body_start, body_end, capability_pos)
        if capability_end is None or capability_end >= register_pos:
            continue

        body_text = text[body_start + 1 : body_end]
        if CAPABILITY_MARKER not in body_text or FEATURE_MESSAGE not in body_text or REGISTER_RETURN not in body_text:
            continue

        seen_bodies.add((body_start, body_end))
        patches.append((body_start, body_end, capability_end))

    return patches


def locate_patched_decision_bodies(data: bytes) -> list[tuple[int, int]]:
    """
    Find already-patched decision-function bodies by looking for blocks that still
    contain the capability gate and register return, but no longer contain the
    downstream auth/allowlist/feature checks.
    """
    text = data.decode("latin-1")
    candidates = []
    seen_bodies = set()

    start = 0
    while True:
        capability_pos = text.find(CAPABILITY_MARKER, start)
        if capability_pos == -1:
            break
        start = capability_pos + 1

        window_start = max(0, capability_pos - 2_500)
        window_end = min(len(text), capability_pos + DECISION_WINDOW)
        register_pos = text.find(REGISTER_RETURN, capability_pos, window_end)
        if register_pos == -1:
            continue

        bounds = find_smallest_enclosing_block(text, capability_pos, capability_pos, register_pos)
        if bounds is None or bounds in seen_bodies:
            continue

        body_start, body_end = bounds
        body_text = text[body_start + 1 : body_end]
        if CAPABILITY_MARKER not in body_text or REGISTER_RETURN not in body_text:
            continue
        if SKIP_RETURN not in body_text:
            continue
        if FEATURE_MESSAGE in body_text or 'kind:"auth"' in body_text or 'kind:"allowlist"' in body_text:
            continue

        seen_bodies.add(bounds)
        candidates.append(bounds)

    return candidates


def looks_like_decision_patched(data: bytes) -> bool:
    support_state, _details = classify_decision_support_patches(data)
    return len(locate_patched_decision_bodies(data)) >= 2 and support_state == "patched"


def apply_decision_patches(data: bytearray) -> int:
    patches = locate_decision_patches(bytes(data))
    if len(patches) < 2:
        return 0

    text = data.decode("latin-1")
    replacements = []
    for body_start, body_end, capability_end in patches:
        preserved = text[body_start + 1 : capability_end]
        replacement = preserved + REGISTER_RETURN
        original_len = body_end - body_start - 1
        if len(replacement) > original_len:
            return 0
        replacement = replacement.ljust(original_len, " ")
        replacements.append((body_start + 1, body_end, replacement))

    for start, end, replacement in reversed(replacements):
        data[start:end] = replacement.encode("latin-1")

    verify_text = data.decode("latin-1")
    for body_start, body_end, _capability_end in patches:
        body_text = verify_text[body_start + 1 : body_end]
        if CAPABILITY_MARKER not in body_text:
            return 0
        if REGISTER_RETURN not in body_text:
            return 0
        if FEATURE_MESSAGE in body_text:
            return 0
        if 'kind:"auth"' in body_text or 'kind:"allowlist"' in body_text:
            return 0

    if len(locate_patched_decision_bodies(data)) < len(replacements):
        return 0
    return len(replacements) + apply_decision_support_patches(data)


def classify_binary(data: bytes) -> tuple[str, str | None, list[str]]:
    details = []
    support_state, support_details = classify_decision_support_patches(data)
    patched_decision_bodies = locate_patched_decision_bodies(data)

    if looks_like_decision_patched(data):
        details.append("decision patch heuristic matched")
        return "patched", "decision", details

    if patched_decision_bodies:
        details.append(f"patched decision-function bodies: {len(patched_decision_bodies)}")
        details.extend(support_details)
        return ("patched", "decision", details) if support_state == "patched" else ("mixed", None, details)

    decision_candidates = locate_decision_patches(data)
    if decision_candidates:
        details.append(f"decision-function candidates: {len(decision_candidates)}")
        details.extend(support_details)
        if patched_decision_bodies:
            return ("patched", "decision", details) if support_state == "patched" else ("mixed", None, details)

    legacy_state, legacy_details = classify_legacy_patch(data)
    details.extend(legacy_details)

    if legacy_state == "patched":
        return "patched", "legacy", details
    if legacy_state == "clean":
        if decision_candidates:
            return "clean", "decision", details
        return "clean", "legacy", details
    return "mixed", None, details


def choose_patch_strategy(data: bytes, requested: str) -> str:
    if requested == "legacy":
        return "legacy"
    if requested == "decision":
        if len(locate_decision_patches(data)) < 2:
            sys.exit("FAIL [decision]: could not safely locate both decision-function copies")
        return "decision"

    if len(locate_decision_patches(data)) >= 2:
        return "decision"
    return "legacy"


def resolve_binary(args_binary: str | None) -> list[Path]:
    return [Path(args_binary)] if args_binary else detect_binaries()


def read_source_bytes(binary: Path) -> tuple[bytes, Path | None]:
    backup = binary.with_suffix(".bak")
    if backup.exists():
        return backup.read_bytes(), backup
    return binary.read_bytes(), None


def revert(binary: Path):
    backup = binary.with_suffix(".bak")
    if not backup.exists():
        sys.exit("No backup found - nothing to revert.")
    shutil.copy2(backup, binary)
    print(f"Reverted -> {binary}")


def write_binary(binary: Path, data: bytes):
    tmp = binary.with_suffix(".patched")
    tmp.write_bytes(data)
    os.chmod(str(tmp), os.stat(str(binary)).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    try:
        os.replace(str(tmp), str(binary))
    except PermissionError:
        sys.exit(
            f"Could not replace {binary} because it is in use. "
            f"Close Claude Code and re-run the command. The patched file is ready at {tmp}."
        )


def check(binary: Path, requested_strategy: str):
    if not binary.exists():
        sys.exit(f"Binary not found: {binary}")

    current = binary.read_bytes()
    backup = binary.with_suffix(".bak")
    status, active_strategy, details = classify_binary(current)

    print(f"Status    : {status}")
    if active_strategy:
        print(f"Detected  : {active_strategy}")
    if backup.exists():
        print(f"Backup    : {backup}")

    source, source_path = read_source_bytes(binary)
    source_label = source_path.name if source_path else binary.name
    strategy = choose_patch_strategy(source, requested_strategy)
    print(f"Would use : {strategy} (from {source_label})")

    for line in details:
        print(f"  - {line}")


def patch(binary: Path, requested_strategy: str):
    if not binary.exists():
        sys.exit(f"Binary not found: {binary}")

    current = binary.read_bytes()
    current_status, current_strategy, _details = classify_binary(current)
    if current_status == "patched" and not binary.with_suffix(".bak").exists():
        print(f"Already patched -> {binary} ({current_strategy})")
        print("No clean .bak exists, so nothing was changed.")
        return

    backup = binary.with_suffix(".bak")
    source, source_path = read_source_bytes(binary)
    if source_path is None:
        shutil.copy2(binary, backup)
        source = backup.read_bytes()
        print(f"Backup  -> {backup}")
    else:
        print("Backup exists, re-patching from clean copy")

    strategy = choose_patch_strategy(source, requested_strategy)
    data = bytearray(source)

    if strategy == "decision":
        edits = apply_decision_patches(data)
        if edits < 2:
            sys.exit("FAIL [decision]: could not safely rewrite both decision-function copies")
    else:
        edits = apply_legacy_patches(data)

    patched_bytes = bytes(data)
    if patched_bytes == current:
        print(f"Already patched -> {binary} ({strategy})")
        return

    write_binary(binary, patched_bytes)
    print(f"\nPatched -> {binary} ({edits} edit block(s), strategy={strategy})")
    print("Run:  claude --channels plugin:telegram@claude-plugins-official")


def main():
    parser = argparse.ArgumentParser(description="Patch Claude Code to enable --channels")
    parser.add_argument("action", nargs="?", default="patch", choices=["patch", "revert"])
    parser.add_argument("--binary", help="Path to claude binary (default: auto-detect all)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Analyze binaries and report patch status without modifying files",
    )
    parser.add_argument(
        "--strategy",
        choices=["auto", "decision", "legacy"],
        default="auto",
        help="Patch strategy: auto tries decision first, then falls back to legacy",
    )
    args = parser.parse_args()

    if args.check and args.action == "revert":
        sys.exit("Cannot use --check with revert.")

    binaries = resolve_binary(args.binary)
    if args.check:
        for binary in binaries:
            print(f"\n{'=' * 60}\n{binary}\n{'=' * 60}")
            check(binary, args.strategy)
        return

    action = revert if args.action == "revert" else (lambda path: patch(path, args.strategy))
    for binary in binaries:
        print(f"\n{'=' * 60}\n{binary}\n{'=' * 60}")
        action(binary)


if __name__ == "__main__":
    main()
