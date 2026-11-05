"""What a bench run was measured on, so that two runs can be compared meaningfully.

  generation      cfg["generation"] = <collection name of a generation registered with
                  scripts/switch_generation.py>: its keyword corpus becomes the corpus and its embedding
                  profile the default embedding. A config that names another corpus, model or prefix scheme
                  is refused.
  profile         the embedding profile of every evaluated configuration (model, revision, prefix scheme,
                  normalization, distance, dimension), recorded next to the corpus SHA-256.
  candidate_miss  1.0 when no relevant evidence is among the candidates: a reranker only reorders what it
                  receives, it cannot rescue such a question.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from eval.bench.encoders import EmbeddingSpec
from eval.bench.metrics import RankedChunk, credit_by_rank
from rag_core import embedding_provider

# hit@1/3/5/10, recall@20/50, MRR@10 and nDCG@10 (the last two come with k=10).
REQUIRED_METRICS = (("hit", (1, 3, 5, 10)), ("recall", (20, 50)), ("mrr", (10,)), ("ndcg", (10,)))


def missing_required_metrics(ks: Sequence[int]) -> List[str]:
    have = {int(k) for k in ks}
    return [f"{name}@{k}" for name, wanted in REQUIRED_METRICS for k in wanted if k not in have]


def embedding_profile(spec: EmbeddingSpec, dimension: int) -> Dict[str, Any]:
    query_prefix, document_prefix = spec.prefixes()
    return {
        "embed_model": spec.model,
        "embed_model_revision": spec.revision,
        "embed_prefix_scheme": embedding_provider.prefix_scheme_name(query_prefix, document_prefix),
        "embed_normalize": "true",  # eval.bench.encoders.encode returns L2-normalized vectors
        "embed_distance": "cosine",
        "embedding_dim": int(dimension),
    }


def candidate_miss(views: Sequence[RankedChunk], query: Any, mode: str) -> Optional[float]:
    """None for an unanswerable query; 1.0 when no relevant evidence is among the views, else 0.0."""
    if not query.answerable:
        return None
    found = any(new > 0 for _gain, new in credit_by_rank(views, query.relevant, mode))
    return 0.0 if found else 1.0


def apply_generation(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """(config with the corpus and embedding of the named generation, generation record); (cfg, None) without one."""
    name = str(cfg.get("generation") or "").strip()
    if not name:
        return cfg, None
    from rag_core import index_manifest

    manifest = index_manifest.read_manifest(name)
    if manifest is None:
        raise ValueError(f"generation '{name}' has no manifest; register it first (scripts/switch_generation.py register)")
    profile = manifest.get("profile") or {}
    model, scheme = profile.get("embed_model"), profile.get("embed_prefix_scheme")
    if not model or not scheme:
        raise ValueError(f"the manifest of generation '{name}' has no embedding model or prefix scheme")
    corpus_path = str(manifest["keyword_index_path"])
    corpus = dict(cfg.get("corpus") or {})
    declared = corpus.get("chunks_jsonl")
    if declared and Path(str(declared)).resolve() != Path(corpus_path).resolve():
        raise ValueError(f"corpus.chunks_jsonl '{declared}' is not the corpus of generation '{name}' ({corpus_path})")
    corpus["chunks_jsonl"] = corpus_path
    specs = cfg.get("embedding")
    if specs is None:
        prefix = "none" if scheme == embedding_provider.PREFIX_SCHEME_NONE else "auto"
        specs = [{"model": str(model), "prefix": prefix}]
    spec_list = list(specs) if isinstance(specs, list) else [specs]
    for spec_cfg in spec_list:
        spec = EmbeddingSpec(**spec_cfg)
        actual = embedding_provider.prefix_scheme_name(*spec.prefixes())
        if spec.model != model or actual != scheme:
            raise ValueError(
                f"the embedding {spec.model!r} (prefix scheme {actual!r}) is not the profile of generation '{name}' "
                f"({model!r}, {scheme!r}); a run about a generation must use its embedding profile"
            )
    resolved = dict(cfg)
    resolved["corpus"] = corpus
    resolved["embedding"] = spec_list
    info = {
        "collection": name,
        "generation_id": manifest.get("generation_id"),
        "backend": manifest.get("backend"),
        "profile": profile,
        "point_count": manifest.get("point_count"),
        "keyword_rows": manifest.get("keyword_rows"),
        "keyword_index_path": corpus_path,
        "verification_status": (manifest.get("verification") or {}).get("status"),
    }
    return resolved, info
