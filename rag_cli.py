from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from rich.console import Console

import config
from rag_core.qa import answer_query
from rag_core.utils import ensure_openai_client


def _setup_logging():
    Path(config.RUNS_DIR).mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("RAG_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(str(Path(config.RUNS_DIR) / "rag_cli.log")),
            logging.StreamHandler(),
        ],
        force=True,
    )


def _run_batch(client):
    console = Console()
    for line in sys.stdin:
        q = line.strip()
        if not q:
            continue
        ans = answer_query(q, client=client, top_k=config.TOP_K, max_context_chars=config.MAX_CONTEXT_CHARS)
        console.print(ans.answer_with_footnotes)


def main():
    _setup_logging()
    client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    if not sys.stdin.isatty():
        _run_batch(client)
        return
    console = Console()
    console.print("RAG CLI: 質問を入力してください。Ctrl-Dで終了。")
    try:
        while True:
            q = input("> ").strip()
            if not q:
                continue
            ans = answer_query(q, client=client, top_k=config.TOP_K, max_context_chars=config.MAX_CONTEXT_CHARS)
            console.print(ans.answer_with_footnotes)
    except EOFError:
        return


if __name__ == "__main__":
    main()
