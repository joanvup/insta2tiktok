from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
MIN_CHUNK_SIZE = 5 * 1024 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024
MAX_TOTAL_CHUNKS = 1000

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class ChunkPlanError(ValueError):
    """El archivo no puede trocearse según las reglas de TikTok."""


@dataclass(frozen=True)
class ChunkPlan:
    total_size: int
    chunk_size: int
    total_chunk_count: int


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "video/mp4")


def plan_chunks(file_size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> ChunkPlan:
    """Calcula el troceado segun las restricciones de TikTok.

    Reglas oficiales:
      - Cada chunk debe tener al menos 5 MB y maximo 64 MB.
      - El ultimo chunk puede superar chunk_size (hasta 128 MB) para absorber
        los bytes sobrantes (ej: 50000123 bytes, chunk 10M -> 4x10M + 10000123).
      - Videos menores de 5 MB se suben enteros, con chunk_size = tamano total.
      - total_chunk_count = video_size // chunk_size (redondeo hacia ABAJO / floor).
      - Maximo 1000 chunks; ultimo chunk maximo 128 MB.

    Nota: con 50.000.123 bytes y chunk de 10.000.000 se obtienen 5 chunks, que
    es exactamente el ejemplo de la documentacion oficial de TikTok.
    """
    if file_size <= 0:
        raise ChunkPlanError("El archivo esta vacio")

    if file_size < MIN_CHUNK_SIZE:
        return ChunkPlan(file_size, file_size, 1)

    if not (MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE):
        raise ChunkPlanError(
            f"chunk_size debe estar entre {MIN_CHUNK_SIZE} y {MAX_CHUNK_SIZE}, recibido {chunk_size}"
        )

    total_chunk_count = file_size // chunk_size

    if total_chunk_count < 1:
        total_chunk_count = 1

    if total_chunk_count > MAX_TOTAL_CHUNKS:
        raise ChunkPlanError(
            f"El video requiere {total_chunk_count} chunks, maximo permitido {MAX_TOTAL_CHUNKS}"
        )

    last_chunk = file_size - (total_chunk_count - 1) * chunk_size
    if last_chunk > 128 * 1024 * 1024:
        raise ChunkPlanError(
            f"El ultimo chunk seria de {last_chunk} bytes, excede el maximo de 128 MB"
        )

    return ChunkPlan(file_size, chunk_size, total_chunk_count)


def iter_chunks(file_path: Path, plan: ChunkPlan) -> Iterator[tuple[int, bytes]]:
    """Itera los chunks respetando el plan calculado.

    Devuelve (indice, datos). Los chunks intermedios tienen exactamente
    chunk_size bytes; el ultimo se queda con todo el resto.
    """
    with file_path.open("rb") as handle:
        yield from _iter_chunks_from_handle(handle, plan)


def _iter_chunks_from_handle(handle: BinaryIO, plan: ChunkPlan) -> Iterator[tuple[int, bytes]]:
    offset = 0
    for index in range(plan.total_chunk_count):
        remaining = plan.total_size - offset
        is_last = index == plan.total_chunk_count - 1
        read_size = remaining if is_last else plan.chunk_size

        data = handle.read(read_size)
        if not data:
            raise ChunkPlanError(
                f"Archivo truncado: se esperaban {plan.total_size} bytes, "
                f"se leyeron {offset} en el chunk {index}"
            )

        yield index, data
        offset += len(data)

    if offset != plan.total_size:
        raise ChunkPlanError(
            f"Total subido {offset} no coincide con video_size {plan.total_size}"
        )