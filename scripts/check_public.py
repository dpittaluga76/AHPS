"""Validate the explicit public export list without traversing private folders."""

import argparse
import html
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
from urllib.parse import unquote, urlsplit


MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_SUFFIXES = {".py", ".c", ".md", ".yml", ".yaml"}
METADATA_FILES = {
    "LICENSE", "pyproject.toml", "MANIFEST.in", ".gitignore", ".gitattributes", ".editorconfig",
    "PUBLIC_FILES.txt", "requirements.txt", "requirements-research.txt",
}
JSON_FILES = {"examples/corpus_manifest.json", "tests/fixtures/v5/default-features.json"}
FIXTURE_FILES = {
    f"tests/fixtures/v3/{case}_{engine}.ahps"
    for case in ("empty", "lcg", "struct", "tiny") for engine in ("c", "py")
}
FIXTURE_HASHES = {f"tests/fixtures/v3/{case}.sha256" for case in ("empty", "lcg", "struct", "tiny")}
FORBIDDEN_PARTS = {
    ".git", ".hg", ".svn", ".aws", ".codex", ".agents", ".venv", "venv",
    "__pycache__", "test corpus", "corpus_development", "corpus_out",
    "experiments", "public-release", "dataset", "datasets", "logs", "build", "dist",
}

# Findings expose only the pattern name and its location, never matched bytes.
SENSITIVE_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "AWS access identifier": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{30,255})\b"),
    "OpenAI token": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,255}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}\b"),
    "credential URL": re.compile(r"https?://[^\s/\"<>:]+:[^\s/\"<>@]+@", re.IGNORECASE),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "secret assignment": re.compile(
        r"(?:api[_-]?key|secret[_-]?(?:key|token)|password|access[_-]?token)"
        r"\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']", re.IGNORECASE
    ),
    "bearer credential": re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    "signed URL credential": re.compile(r"[?&](?:sig|X-Amz-Signature)=[A-Za-z0-9%+/=_-]{16,}", re.IGNORECASE),
    "private home path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\s\\/\"'<>]+|/(?:Users|home)/[^\s/\"'<>]+", re.IGNORECASE),
}
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[\"'][^\n]*?[\"'])?\s*\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))", re.MULTILINE)


def _path_problem(name: str):
    if not name or "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        return "invalid relative path"
    if any(char in name for char in "*?[]{}"):
        return "globs are forbidden"
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        return "path must be canonical and relative"
    if any(part in (".", "..") or part.rstrip(" .") != part for part in path.parts):
        return "traversal or ambiguous path component"
    if any(part.casefold() in FORBIDDEN_PARTS for part in path.parts):
        return "private or generated directory is forbidden"
    if name in METADATA_FILES or name in JSON_FILES or name in FIXTURE_FILES or name in FIXTURE_HASHES:
        return None
    if path.suffix not in ALLOWED_SUFFIXES:
        return "file type is outside the public allowlist policy"
    return None


def _checked_file(root: Path, name: str) -> Path:
    """Check each component before opening it, including Windows junctions."""
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        info = current.lstat()
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(info.st_mode) or reparse:
            raise ValueError("symlinks and reparse points are forbidden")
    if not current.resolve().is_relative_to(root) or not current.is_file():
        raise ValueError("entry must be a regular file inside the export root")
    if current.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file exceeds the 2 MiB public limit")
    return current


def read_public_files(root: Path) -> list[str]:
    """Read sorted, unique, explicit relative paths, allowing comment lines."""
    root = Path(root).resolve()
    try:
        text = _checked_file(root, "PUBLIC_FILES.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("PUBLIC_FILES.txt is missing, unsafe, oversized, or invalid UTF-8") from exc
    files = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        problem = _path_problem(line)
        if line != line.strip():
            problem = "path has surrounding whitespace"
        if problem:
            raise ValueError(f"PUBLIC_FILES.txt:{number}: {problem}")
        files.append(line)
    if not files or files != sorted(set(files)) or len({name.casefold() for name in files}) != len(files):
        raise ValueError("PUBLIC_FILES.txt must contain sorted, unique paths without case collisions")
    if "PUBLIC_FILES.txt" not in files:
        raise ValueError("PUBLIC_FILES.txt must include itself")
    return files


def _markdown_prose(text: str) -> str:
    lines = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            lines.append("\n")
        elif marker:
            fence = marker[1]
            lines.append("\n")
        else:
            lines.append(re.sub(r"(`+)[^\n]*?\1", lambda match: " " * len(match[0]), line))
    return "".join(lines)


def _markdown_issues(name: str, text: str, selected: set[str]) -> list[str]:
    prose = _markdown_prose(text)
    issues = []
    for pattern in (INLINE_LINK, REFERENCE_LINK):
        for match in pattern.finditer(prose):
            number = prose.count("\n", 0, match.start()) + 1
            target = html.unescape(match[1] or match[2])
            try:
                parsed = urlsplit(target)
            except ValueError:
                issues.append(f"{name}:{number}: malformed Markdown link")
                continue
            if parsed.scheme in {"http", "https", "mailto", "tel", "data"} or target.startswith("//"):
                continue
            if not parsed.path and not parsed.scheme:
                continue
            path = unquote(parsed.path)
            if parsed.scheme or path.startswith("/") or "\\" in path:
                issues.append(f"{name}:{number}: Markdown link must use a public relative path")
                continue
            resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), path))
            if resolved not in selected:
                issues.append(f"{name}:{number}: Markdown link target is outside PUBLIC_FILES.txt")
    return issues


def validate_public_tree(root: Path) -> list[str]:
    """Validate listed files only; callers must export only this explicit list."""
    root = Path(root).resolve()
    try:
        files = read_public_files(root)
    except ValueError as exc:
        return [str(exc)]
    issues = []
    selected = set(files)
    for name in files:
        try:
            data = _checked_file(root, name).read_bytes()
        except ValueError as exc:
            issues.append(f"{name}: {exc}")
            continue
        except OSError:
            issues.append(f"{name}: listed file is missing or unreadable")
            continue
        if name in FIXTURE_FILES:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            issues.append(f"{name}: text file is not valid UTF-8")
            continue
        if "\0" in text:
            issues.append(f"{name}: text file contains binary NUL bytes")
        for category, pattern in SENSITIVE_PATTERNS.items():
            for match in pattern.finditer(text):
                number = text.count("\n", 0, match.start()) + 1
                issues.append(f"{name}:{number}: possible {category}")
        if name.endswith(".md"):
            issues.extend(_markdown_issues(name, text, selected))
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    issues = validate_public_tree(args.tree)
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print("Public export validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
