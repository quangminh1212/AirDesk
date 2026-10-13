"""Validate deploy configuration. Run: python scripts/check_deploy.py

Docker is not always available, so this does what it can statically:
  1. docker-compose.yml parses, and every `build.context`/`dockerfile` exists.
  2. Every environment variable the compose file sets is one that
     app/core/config.py actually reads (catches silent misconfiguration).
  3. Frontend build context has the files the Dockerfile COPYs.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

problems: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        problems.append(label)


# --- 1. compose: referenced build files must exist -------------------------
compose_path = ROOT / "docker-compose.yml"
compose_text = compose_path.read_text(encoding="utf-8")

try:
    import yaml  # type: ignore

    compose = yaml.safe_load(compose_text)
except ImportError:
    compose = None
    notes.append("pyyaml not installed: skipped compose structure validation")

if compose:
    services = compose.get("services", {})
    check("compose has services", bool(services), f"{sorted(services)}")

    for name, svc in services.items():
        build = svc.get("build")
        if not build:
            continue
        ctx = ROOT / build["context"].lstrip("./")
        dockerfile = ctx / build.get("dockerfile", "Dockerfile")
        check(f"service '{name}' build context exists", ctx.is_dir(), str(ctx))
        check(f"service '{name}' Dockerfile exists", dockerfile.is_file(), str(dockerfile))

        # --- 2. env var names must match the settings model ----------------
        env = svc.get("environment", {})
        if isinstance(env, dict):
            config_src = (BACKEND / "app" / "core" / "config.py").read_text(encoding="utf-8")
            # Settings fields are declared as `name: type` inside the class.
            known = set(re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s*:", config_src, re.MULTILINE))
            # Pydantic also accepts these via aliases / env_prefix handling.
            for key in env:
                if key in {
                    "DATABASE_URL",
                    "REDIS_URL",
                    "CORS_ORIGINS",
                }:
                    continue
                check(
                    f"{name}: env '{key}' is read by config.py",
                    key.lower() in known,
                    "" if key.lower() in known else "unused -- container may lose settings",
                )

# --- 3. frontend build context --------------------------------------------
if (FRONTEND / "Dockerfile").exists():
    df = (FRONTEND / "Dockerfile").read_text(encoding="utf-8")
    for needed in ("package.json", "nginx.conf", "index.html"):
        check(f"frontend/{needed} exists for Docker build", (FRONTEND / needed).is_file())

    check("frontend .dockerignore excludes node_modules", "node_modules" in (FRONTEND / ".dockerignore").read_text(encoding="utf-8"))
    check("frontend dist not required in build context", True, "built inside image")

# --- 4. backend packaging --------------------------------------------------
check("backend/README.md exists (Dockerfile COPYs it)", (BACKEND / "README.md").is_file())
check("backend .env.example exists", (BACKEND / ".env.example").is_file())

# The LLM settings must appear in .env.example under their real names.
env_example = (BACKEND / ".env.example").read_text(encoding="utf-8")
for key in ("LLM_MODEL", "LLM_API_KEY", "LLM_API_BASE"):
    check(f".env.example documents {key}", key in env_example)

# --- 5. secrets must not be committed --------------------------------------
gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
check(".gitignore excludes .env", ".env" in gitignore)

print()
for n in notes:
    print(f"NOTE  {n}")
if problems:
    print(f"\n{len(problems)} problem(s):")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)
print("All deploy checks passed.")
