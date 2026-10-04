import io
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://example.com/callback")

from app.tiktok.chunking import (
    DEFAULT_CHUNK_SIZE,
    ChunkPlan,
    ChunkPlanError,
    _iter_chunks_from_handle,
    content_type_for,
    plan_chunks,
)

MB = 1024 * 1024


class TestPlanChunks:
    def test_ejemplo_oficial_tiktok(self):
        # Ejemplo literal de la documentación de TikTok:
        # video_size=50000123, chunk_size=10000000, total_chunk_count=5
        plan = plan_chunks(50_000_123, chunk_size=10_000_000)
        assert plan.chunk_size == 10_000_000
        assert plan.total_chunk_count == 5

    def test_chunk_size_por_defecto_es_10mib(self):
        plan = plan_chunks(100 * MB)
        assert plan.chunk_size == DEFAULT_CHUNK_SIZE == 10 * MB
        assert plan.total_chunk_count == 100 * MB // (10 * MB)

    def test_chunk_size_fuera_de_rango(self):
        with pytest.raises(ChunkPlanError):
            plan_chunks(100 * MB, chunk_size=1 * MB)
        with pytest.raises(ChunkPlanError):
            plan_chunks(100 * MB, chunk_size=128 * MB)

    def test_archivo_pequeno_enteros(self):
        plan = plan_chunks(4 * MB)
        assert plan.chunk_size == 4 * MB
        assert plan.total_chunk_count == 1

    def test_exactamente_un_chunk(self):
        plan = plan_chunks(10 * MB)
        assert plan.chunk_size == 10 * MB
        assert plan.total_chunk_count == 1

    def test_dos_chunks(self):
        plan = plan_chunks(20 * MB)
        assert plan.total_chunk_count == 2
        assert plan.chunk_size == 10 * MB

    def test_sin_chunk_enano_final(self):
        plan = plan_chunks(50_000_123, chunk_size=10_000_000)
        ultimo = plan.total_size - (plan.total_chunk_count - 1) * plan.chunk_size
        assert ultimo >= 5 * MB

    def test_tamano_cero(self):
        with pytest.raises(ChunkPlanError):
            plan_chunks(0)

    def test_las_partes_suman_total(self):
        for size in (50_000_123, 4 * MB, 10 * MB, 20 * MB, 130 * MB):
            plan = plan_chunks(size)
            partes = [plan.chunk_size] * (plan.total_chunk_count - 1)
            partes.append(size - sum(partes))
            assert sum(partes) == size
            assert min(partes) >= 5 * MB or plan.total_chunk_count == 1

    def test_ultimo_chunk_nunca_supera_128mb(self):
        for size in (5 * MB, 50 * MB, 500 * MB, 2 * 1024 * MB):
            plan = plan_chunks(size)
            ultimo = plan.total_size - (plan.total_chunk_count - 1) * plan.chunk_size
            assert ultimo <= 128 * MB


class TestIterChunks:
    def test_un_chunk_absorbe_los_bytes_sobrantes(self, tmp_path):
        """10 MiB + 123 bytes cabe en UN chunk: el resto se fusiona."""
        data = b"A" * (10 * MB) + b"B" * 123
        f = tmp_path / "video.mp4"
        f.write_bytes(data)

        plan = plan_chunks(len(data), chunk_size=10 * MB)
        assert plan.total_chunk_count == 1

        chunks = list(_open_file_chunks(f, plan))
        assert len(chunks) == 1
        assert chunks[0] == (0, data)

    def test_dos_chunks_ultimo_absorbe_resto(self, tmp_path):
        """20 MiB + 123 bytes con chunks de 10 MiB -> 2 chunks."""
        data = b"A" * (20 * MB) + b"B" * 123
        f = tmp_path / "video.mp4"
        f.write_bytes(data)

        plan = plan_chunks(len(data), chunk_size=10 * MB)
        assert plan.total_chunk_count == 2

        chunks = list(_open_file_chunks(f, plan))
        assert len(chunks) == 2
        assert chunks[0] == (0, b"A" * (10 * MB))
        assert chunks[1] == (1, b"A" * (10 * MB) + b"B" * 123)
        assert b"".join(c for _, c in chunks) == data

    def test_archivo_truncado_lanza_error(self):
        plan = ChunkPlan(total_size=100, chunk_size=10, total_chunk_count=1)
        fake = MagicMock()
        fake.read.return_value = b""
        with pytest.raises(ChunkPlanError):
            list(_iter_chunks_from_handle(fake, plan))

    def test_total_incoherente_lanza_error(self):
        plan = ChunkPlan(total_size=100, chunk_size=10, total_chunk_count=2)
        fake = MagicMock()
        fake.read.side_effect = [b"x" * 10, b"y" * 5]
        with pytest.raises(ChunkPlanError):
            list(_iter_chunks_from_handle(fake, plan))


def _open_file_chunks(path: Path, plan: ChunkPlan):
    with path.open("rb") as handle:
        yield from _iter_chunks_from_handle(handle, plan)


class TestContentType:
    def test_mp4(self):
        assert content_type_for(Path("a.mp4")) == "video/mp4"

    def test_mov(self):
        assert content_type_for(Path("a.mov")) == "video/quicktime"

    def test_webm(self):
        assert content_type_for(Path("a.webm")) == "video/webm"

    def test_mayusculas(self):
        assert content_type_for(Path("A.MP4")) == "video/mp4"