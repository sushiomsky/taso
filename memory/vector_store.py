"""
TASO – FAISS vector store for semantic memory.

Stores text chunks with metadata and allows similarity search.
Falls back gracefully when FAISS or sentence-transformers are not
installed (logs a warning and uses an in-memory no-op store).

Storage:
  - FAISS index: settings.VECTOR_INDEX_PATH
  - Metadata:    settings.VECTOR_META_PATH (pickled list of dicts)
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from config.logging_config import get_logger

log = get_logger("agent")

# ---------------------------------------------------------------------------
# Optional heavy imports
# ---------------------------------------------------------------------------
try:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    _FAISS_OK = True
except ImportError:
    _FAISS_OK = False
    log.warning(
        "faiss-cpu or sentence-transformers not installed – "
        "VectorStore will operate in degraded (no-embedding) mode."
    )

_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384  # fixed for all-MiniLM-L6-v2


class VectorStore:
    """
    Semantic vector store backed by FAISS.

    Each entry stores:
      - raw text
      - a category tag  (e.g. "threat_intel", "analysis", "conversation")
      - arbitrary metadata dict
    """

    def __init__(
        self,
        index_path: Optional[Path] = None,
        meta_path: Optional[Path] = None,
    ) -> None:
        self._index_path = index_path or settings.VECTOR_INDEX_PATH
        self._meta_path = meta_path or settings.VECTOR_META_PATH
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

        self._index: Any = None   # faiss.Index
        self._meta: List[Dict] = []
        self._model: Any = None   # SentenceTransformer
        self._ready = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load (or create) the FAISS index and metadata from disk."""
        if not _FAISS_OK:
            log.warning("VectorStore.load skipped – FAISS not available.")
            return

        # Load or create FAISS index
        if self._index_path.exists():
            self._index = faiss.read_index(str(self._index_path))
            log.info(f"FAISS index loaded: {self._index.ntotal} vectors")
        else:
            self._index = faiss.IndexFlatL2(_EMBEDDING_DIM)
            log.info("FAISS index created (empty).")

        # Load or create metadata
        if self._meta_path.exists():
            with open(self._meta_path, "rb") as fh:
                self._meta = pickle.load(fh)
        else:
            self._meta = []

        # Load the embedding model (cached by sentence-transformers)
        self._model = SentenceTransformer(_EMBEDDING_MODEL)
        self._ready = True
        log.info("VectorStore ready.")

    def save(self) -> None:
        """Persist FAISS index and metadata to disk."""
        if not self._ready:
            return
        faiss.write_index(self._index, str(self._index_path))
        with open(self._meta_path, "wb") as fh:
            pickle.dump(self._meta, fh)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add(self, text: str, category: str = "general",
            metadata: Optional[Dict] = None) -> int:
        """
        Embed *text* and store it.

        Returns the integer ID of the new entry, or -1 if unavailable.
        """
        if not self._ready:
            return -1

        vec = self._embed([text])  # shape (1, DIM)
        self._index.add(vec)

        entry_id = len(self._meta)
        self._meta.append({
            "id": entry_id,
            "text": text,
            "category": category,
            "meta": metadata or {},
        })
        self.save()
        return entry_id

    def add_bulk(self, texts: List[str], category: str = "general",
                  metadata: Optional[List[Dict]] = None) -> List[int]:
        """Embed and store a list of texts in one batch."""
        if not self._ready or not texts:
            return []

        vecs = self._embed(texts)
        self._index.add(vecs)

        ids = []
        for i, text in enumerate(texts):
            entry_id = len(self._meta)
            self._meta.append({
                "id": entry_id,
                "text": text,
                "category": category,
                "meta": (metadata[i] if metadata else {}),
            })
            ids.append(entry_id)

        self.save()
        return ids

    def search(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> List[Dict]:
        """
        Return the top-k most similar stored entries for *query*.

        Each result dict contains: id, text, category, meta, score.
        """
        if not self._ready or self._index.ntotal == 0:
            return []

        vec = self._embed([query])
        distances, indices = self._index.search(vec, min(top_k * 3, self._index.ntotal))

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._meta):
                continue
            entry = dict(self._meta[idx])
            entry["score"] = float(dist)
            if category and entry["category"] != category:
                continue
            results.append(entry)
            if len(results) >= top_k:
                break

        return results

    def count(self) -> int:
        if not self._ready:
            return 0
        return self._index.ntotal

    def clear(self) -> None:
        """Remove all vectors (non-recoverable)."""
        if not self._ready:
            return
        self._index = faiss.IndexFlatL2(_EMBEDDING_DIM)
        self._meta = []
        self.save()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _embed(self, texts: List[str]):  # -> np.ndarray
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return np.array(vecs, dtype="float32")
