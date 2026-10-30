"""The administration console: the built single-page app of webui/, served as static files under /console/ (S1).

The page and its scripts are public files (like the older login page); every call the app makes to the API is
authenticated there. A path inside the app that is not a file (a route of the app) gets index.html, so a reload works.
A path that leaves the build directory is never served. Without a build the console answers 404 and says so.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import config

router = APIRouter()


def dist_dir() -> Path:
    configured = str(os.getenv("CONSOLE_DIST_DIR", "") or "").strip()
    return Path(configured) if configured else Path(config.BASE_DIR) / "webui" / "dist"


@router.get("/console", include_in_schema=False)
def console_root():
    return RedirectResponse(url="/console/", status_code=307)


@router.get("/console/{path:path}", include_in_schema=False)
def console_file(path: str):
    root = dist_dir().resolve()
    index = root / "index.html"
    if not index.is_file():
        return JSONResponse(status_code=404, content={"detail": "the console is not built (see webui/README.md)"})
    target = (root / path).resolve() if path else index
    if root not in target.parents and target != root:
        return JSONResponse(status_code=404, content={"detail": "not found"})
    if target.is_file():
        # Built assets carry a content hash in their name: they never change. Everything else is read again every time.
        immutable = "assets" in target.relative_to(root).parts
        cache = "public, max-age=31536000, immutable" if immutable else "no-store"
        return FileResponse(target, headers={"Cache-Control": cache})
    if "." in path.rsplit("/", 1)[-1]:  # a missing file (a script, an image): not the app
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return FileResponse(index, headers={"Cache-Control": "no-store"})
