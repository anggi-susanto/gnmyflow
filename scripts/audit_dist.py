"""Fail closed if distribution members fall outside the release allowlist."""

import hashlib
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def allowed(name, *, wheel):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if wheel:
        if name.startswith("studiowright/"):
            return path.suffix == ".py"
        return len(path.parts) >= 2 and path.parts[0].startswith("gnmyflow-") and (
            path.parts[0].endswith(".dist-info")
        ) and (
            name.endswith(
                ("/METADATA", "/WHEEL", "/RECORD", "/entry_points.txt", "/licenses/LICENSE")
            )
        )
    if name.startswith(("src/studiowright/", "tests/")):
        return path.suffix == ".py"
    return name in {
        "pyproject.toml", "README.md", "LICENSE", "PKG-INFO", ".gitignore",
        "scripts/audit_dist.py", "scripts/smoke_install.py",
        ".claude-plugin/marketplace.json",
        "plugins/gnmyflow/.mcp.json",
        "plugins/gnmyflow/.claude-plugin/plugin.json",
        "plugins/gnmyflow/skills/gnmyflow/SKILL.md",
    }


def audit(directory):
    artifacts = sorted(path for path in directory.iterdir() if path.name != ".gitignore")
    assert len(artifacts) == 2, artifacts
    assert sum(p.suffix == ".whl" for p in artifacts) == 1
    assert sum(p.name.endswith(".tar.gz") for p in artifacts) == 1
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
            assert all(allowed(name, wheel=True) for name in names), names
            assert any(name.endswith("/licenses/LICENSE") for name in names)
        else:
            with tarfile.open(artifact) as archive:
                members = archive.getmembers()
            root = artifact.name.removesuffix(".tar.gz") + "/"
            assert all(m.isfile() and m.name.startswith(root) for m in members), members
            names = [m.name.removeprefix(root) for m in members]
            assert all(allowed(name, wheel=False) for name in names), names
            assert "LICENSE" in names
        print(artifact.name, hashlib.sha256(artifact.read_bytes()).hexdigest(), len(names), "files")


if __name__ == "__main__":
    audit(Path("dist"))
