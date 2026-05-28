"""Leitura controlada de documentos de referencia associados a uma iniciativa."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile
from xml.etree import ElementTree


SUPPORTED_SUFFIXES = {".md", ".txt", ".rst", ".docx"}
IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", "data"}
MAX_SOURCE_BYTES = 2_000_000
MAX_CONTEXT_CHARS = 18_000
MAX_DOCUMENT_OPTIONS = 60


def _safe_workspace_root(workspace_root: str | Path) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("O workspace informado nao existe ou nao e um diretorio.")
    return root


def _resolve_reference_path(workspace_root: str | Path, document_ref: str) -> tuple[Path, Path]:
    root = _safe_workspace_root(workspace_root)
    clean_ref = document_ref.strip().replace("\\", "/").removeprefix("./")
    if not clean_ref:
        raise ValueError("Selecione um documento de referencia.")
    candidate = (root / clean_ref).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("O documento de referencia deve estar dentro do workspace.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("Documento de referencia nao encontrado no workspace.")
    if candidate.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ValueError("Use documentos .md, .txt, .rst ou .docx como referencia.")
    if candidate.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Documento excede o limite de 2 MB para contexto controlado.")
    return root, candidate


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            source = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError("O arquivo DOCX nao pode ser lido como documento valido.") from error
    root = ElementTree.fromstring(source)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def load_reference_document(workspace_root: str | Path, document_ref: str) -> dict:
    root, path = _resolve_reference_path(workspace_root, document_ref)
    raw_text = (
        _docx_text(path)
        if path.suffix.casefold() == ".docx"
        else path.read_text(encoding="utf-8", errors="replace")
    )
    normalized = raw_text.replace("\x00", "").strip()
    content = normalized[:MAX_CONTEXT_CHARS]
    relative_ref = path.relative_to(root).as_posix()
    return {
        "document_ref": relative_ref,
        "document_name": path.name,
        "document_format": path.suffix.casefold().removeprefix("."),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "character_count": len(normalized),
        "included_characters": len(content),
        "truncated": len(normalized) > MAX_CONTEXT_CHARS,
        "content": content,
    }


def list_reference_documents(workspace_root: str | Path) -> list[dict]:
    root = _safe_workspace_root(workspace_root)
    candidates: list[Path] = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in IGNORED_DIRS
        )
        for filename in filenames:
            path = Path(directory) / filename
            if path.suffix.casefold() in SUPPORTED_SUFFIXES and path.stat().st_size <= MAX_SOURCE_BYTES:
                candidates.append(path)
    candidates.sort(
        key=lambda path: (len(path.relative_to(root).parts), path.relative_to(root).as_posix().casefold())
    )
    return [
        {
            "document_ref": path.relative_to(root).as_posix(),
            "document_name": path.name,
            "document_format": path.suffix.casefold().removeprefix("."),
            "size_bytes": path.stat().st_size,
        }
        for path in candidates[:MAX_DOCUMENT_OPTIONS]
    ]
