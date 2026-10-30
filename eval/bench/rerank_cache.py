"""Cross-encoder scoring with an on-disk score cache.

A depth sweep scores the deepest candidate list once; shallower depths reuse
the cached scores. Compute time per pair is measured on cache misses and
persisted, so rerank latency for a depth can be estimated as
depth x seconds-per-pair even when every score came from the cache.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Sequence


class CrossEncoderScorer:
    def __init__(
        self, model: str, cache_dir: Path, *, max_length: int = 512, batch_size: int = 32,
        revision: str | None = None, local_model_path: str | None = None,
    ):
        self.model_name = model
        self.revision = revision
        self.local_model_path = local_model_path
        self.max_length = int(max_length)
        self.batch_size = int(batch_size)
        key = hashlib.sha1(
            f"{model}\x00{revision or ''}\x00{local_model_path or ''}\x00{self.max_length}".encode("utf-8")
        ).hexdigest()[:16]
        self.cache_path = Path(cache_dir) / "rerank" / f"{key}.jsonl"
        self.meta_path = Path(cache_dir) / "rerank" / f"{key}.meta.json"
        self.cache: Dict[str, float] = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["k"]] = float(row["s"])
        meta = json.loads(self.meta_path.read_text(encoding="utf-8")) if self.meta_path.exists() else {}
        self.pairs_computed = int(meta.get("pairs", 0))
        self.seconds_computed = float(meta.get("seconds", 0.0))
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            kwargs = {"max_length": self.max_length}
            if self.revision:
                kwargs["revision"] = self.revision
            if self.local_model_path:
                kwargs["local_files_only"] = True
                kwargs["trust_remote_code"] = False
            self._model = CrossEncoder(self.local_model_path or self.model_name, **kwargs)
        return self._model

    @staticmethod
    def _key(query: str, text: str) -> str:
        return hashlib.sha1(f"{query}\x00{text}".encode("utf-8")).hexdigest()

    def scores(self, query: str, texts: Sequence[str]) -> List[float]:
        keys = [self._key(query, text) for text in texts]
        missing = [i for i, key in enumerate(keys) if key not in self.cache]
        if missing:
            model = self._load()
            started = time.perf_counter()
            predicted = model.predict([(query, texts[i]) for i in missing], batch_size=self.batch_size)
            elapsed = time.perf_counter() - started
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as f:
                for i, score in zip(missing, predicted):
                    self.cache[keys[i]] = float(score)
                    f.write(json.dumps({"k": keys[i], "s": float(score)}) + "\n")
            self.pairs_computed += len(missing)
            self.seconds_computed += elapsed
            self.meta_path.write_text(
                json.dumps({"model": self.model_name, "max_length": self.max_length, "pairs": self.pairs_computed, "seconds": self.seconds_computed}),
                encoding="utf-8",
            )
        return [self.cache[key] for key in keys]

    def seconds_per_pair(self) -> float:
        """Measured mean compute time per pair (batched); NaN before any compute."""
        if self.pairs_computed == 0:
            return float("nan")
        return self.seconds_computed / self.pairs_computed
