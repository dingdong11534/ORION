"""Application-layer sensitive path classification rules."""

from pathlib import Path

# Sensitive path components and file names strictly blocked from inspection or dispatch
SENSITIVE_NAMES: frozenset[str] = frozenset(
    [
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
        ".gcloud",
        ".bashrc",
        ".bash_profile",
        ".profile",
        ".zshrc",
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".pgpass",
        ".my.cnf",
        ".htpasswd",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "id_ed25519_sk",
        "id_ecdsa_sk",
        "authorized_keys",
        "known_hosts",
        "shadow",
        "passwd",
        "credentials",
        "credentials.json",
        "application_default_credentials.json",
        "token.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    ]
)

SENSITIVE_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".ppk",
    ".crt",
    ".cer",
    ".der",
)


def is_sensitive_filename(name: str) -> bool:
    """Return True if a filename or path component matches a blocked sensitive pattern."""
    lower = name.strip().lower()
    if not lower:
        return False
    if lower in SENSITIVE_NAMES:
        return True
    if lower.startswith(".env."):
        return True
    return lower.endswith(SENSITIVE_SUFFIXES)


def is_sensitive_relative_path(raw_path: str) -> bool:
    """Return True if any path component in raw_path is sensitive."""
    if not raw_path:
        return False
    return any(is_sensitive_filename(part) for part in Path(raw_path).parts)
