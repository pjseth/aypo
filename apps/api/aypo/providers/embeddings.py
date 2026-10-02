"""Text embeddings.

Claude has no embeddings endpoint, so embeddings come from a separate provider
behind this interface. 1536 dimensions is deliberate: pgvector's HNSW index
supports at most 2000 dimensions, so text-embedding-3-large is requested with
dimensions=1536 rather than its native 3072 -- keeping full index support at
close to full quality.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog

from aypo.config import get_settings
from aypo.llm.pricing import Ledger, Spend, embedding_cost_usd

log = structlog.get_logger(__name__)

# OpenAI's embeddings endpoint accepts batches; 128 keeps each request well
# inside the per-request token ceiling for documents of the size we embed.
_BATCH = 128


class EmbeddingsUnavailable(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddings(EmbeddingProvider):
    def __init__(self, api_key: str, model: str, dimensions: int, ledger: Ledger | None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions
        self._ledger = ledger

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            chunk = texts[start : start + _BATCH]
            # An empty string is a valid input to the API but a meaningless
            # vector; callers should filter, but guard so one bad row cannot
            # fail an entire batch of 128.
            safe = [t if t.strip() else " " for t in chunk]
            resp = await self._client.embeddings.create(
                model=self.model, input=safe, dimensions=self.dimensions
            )
            out.extend(item.embedding for item in resp.data)
            if self._ledger is not None:
                tokens = getattr(resp.usage, "total_tokens", 0) or 0
                self._ledger.record(
                    Spend(
                        kind="embedding",
                        model=self.model,
                        usd=embedding_cost_usd(self.model, tokens),
                        input_tokens=tokens,
                        label="embed",
                    )
                )
        return out


class JinaEmbeddings(EmbeddingProvider):
    """Fallback provider, used when OpenAI is unavailable or rate-limited."""

    def __init__(self, api_key: str, dimensions: int, ledger: Ledger | None) -> None:
        self._key = api_key
        self.dimensions = dimensions
        self.model = "jina-embeddings-v3"
        self._ledger = ledger

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(texts), _BATCH):
                chunk = [t if t.strip() else " " for t in texts[start : start + _BATCH]]
                resp = await client.post(
                    "https://api.jina.ai/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={
                        "model": self.model,
                        "input": chunk,
                        "dimensions": self.dimensions,
                        "task": "text-matching",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                out.extend(item["embedding"] for item in body["data"])
                if self._ledger is not None:
                    tokens = (body.get("usage") or {}).get("total_tokens", 0) or 0
                    self._ledger.record(
                        Spend(
                            kind="embedding",
                            model=self.model,
                            usd=embedding_cost_usd(self.model, tokens),
                            input_tokens=tokens,
                            label="embed",
                        )
                    )
        return out


def get_embedding_provider(ledger: Ledger | None = None) -> EmbeddingProvider:
    settings = get_settings()
    dims = settings.aypo_embedding_dimensions
    if dims > 2000:
        raise ValueError(
            f"AYPO_EMBEDDING_DIMENSIONS={dims} exceeds pgvector's 2000-dimension HNSW limit"
        )
    if settings.aypo_embedding_provider == "openai":
        key = settings.secret("openai_api_key")
        if key:
            return OpenAIEmbeddings(key, settings.aypo_embedding_model, dims, ledger)
        jina = settings.secret("jina_api_key")
        if jina:
            log.warning("embeddings.openai_unconfigured_using_jina")
            return JinaEmbeddings(jina, dims, ledger)
    else:
        key = settings.secret("jina_api_key")
        if key:
            return JinaEmbeddings(key, dims, ledger)
    raise EmbeddingsUnavailable(
        "no embedding credential configured; set OPENAI_API_KEY or JINA_API_KEY. "
        "Clustering, semantic search and theme matching are unavailable without it"
    )


async def embed_one(text: str, ledger: Ledger | None = None) -> list[float]:
    return (await get_embedding_provider(ledger).embed([text]))[0]
