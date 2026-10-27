"""Concurrent request coalescing and bounded caching for embeddings."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(frozen=True, slots=True)
class _BatchResult:
    vectors: tuple[EmbeddingVector, ...]
    usage: InferenceUsage
    batch_id: int


@dataclass(frozen=True, slots=True)
class _Flight:
    batch: asyncio.Future[_BatchResult]
    index: int


class CoalescingEmbeddingModel:
    """Share in-flight embeddings and cache successful vectors in an LRU.

    Instances coordinate calls made on one event loop. Cached values are scoped to
    this wrapper and its fixed underlying model/profile.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError
        if coalesce_ms < 0:
            raise ValueError
        if cache_size < 0:
            raise ValueError
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, _Flight] = {}
        self._pending: OrderedDict[str, asyncio.Future[_BatchResult]] = OrderedDict()
        self._worker: asyncio.Task[None] | None = None
        self._next_batch_id = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=())

        loop = asyncio.get_running_loop()
        resolved: dict[str, EmbeddingVector] = {}
        flights: dict[str, _Flight] = {}
        for text in dict.fromkeys(texts):
            vector = self._cache.get(text)
            if vector is not None:
                self._cache.move_to_end(text)
                resolved[text] = vector
                continue
            flight = self._inflight.get(text)
            if flight is None:
                batch = loop.create_future()
                # Retrieve an unobserved failure if every interested caller is cancelled.
                batch.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                flight = _Flight(batch=batch, index=0)
                self._inflight[text] = flight
                self._pending[text] = batch
            flights[text] = flight

        if self._pending and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._dispatch())

        waited: dict[asyncio.Future[_BatchResult], _BatchResult] = {}
        for text, flight in flights.items():
            if flight.batch not in waited:
                waited[flight.batch] = await asyncio.shield(flight.batch)
            resolved[text] = waited[flight.batch].vectors[flight.index]

        ordered = tuple(resolved[text] for text in texts)
        unique_batches = {result.batch_id: result for result in waited.values()}
        usage = _sum_usage(result.usage for result in unique_batches.values())
        return EmbeddingResult(vectors=ordered, usage=usage)

    async def _dispatch(self) -> None:
        if self._coalesce_seconds:
            await asyncio.sleep(self._coalesce_seconds)
        while self._pending:
            items = [self._pending.popitem(last=False) for _ in range(min(self._max_batch_size, len(self._pending)))]
            texts = tuple(text for text, _ in items)
            batches = tuple(batch for _, batch in items)
            for text, batch in items:
                self._inflight[text] = _Flight(batch=batch, index=0)
            await self._run_batch(texts, batches)

    async def _run_batch(self, texts: tuple[str, ...], batches: tuple[asyncio.Future[_BatchResult], ...]) -> None:
        batch_id = self._next_batch_id
        self._next_batch_id += 1
        try:
            batch_results = await self._embed_batch(texts, batch_id)
            for batch, batch_result in zip(batches, batch_results, strict=True):
                batch.set_result(batch_result)
        except BaseException as error:
            for batch in batches:
                if not batch.done():
                    batch.set_exception(error)
        finally:
            for text in texts:
                flight = self._inflight.get(text)
                if flight is not None and flight.batch in batches:
                    self._inflight.pop(text, None)

    async def _embed_batch(self, texts: tuple[str, ...], batch_id: int) -> tuple[_BatchResult, ...]:
        result = await self._model.embed(texts)
        if len(result.vectors) != len(texts):
            raise ValueError
        for text, vector in zip(texts, result.vectors, strict=True):
            if self._cache_size:
                self._cache[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return tuple(
            _BatchResult(vectors=(vector,), usage=result.usage, batch_id=batch_id) for vector in result.vectors
        )


def _sum_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    values = tuple(usages)
    return InferenceUsage(
        requests=sum(value.requests for value in values),
        input_tokens=_sum_optional(value.input_tokens for value in values),
        output_tokens=_sum_optional(value.output_tokens for value in values),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = tuple(value for value in values if value is not None)
    return sum(present) if present else None


__all__ = ["CoalescingEmbeddingModel"]
