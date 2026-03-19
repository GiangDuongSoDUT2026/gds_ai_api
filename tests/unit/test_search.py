import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "api"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "shared"))


def test_search_request_defaults():
    from api.schemas.search import SearchRequest

    req = SearchRequest(q="machine learning")
    assert req.mode == "keyword"
    assert req.limit == 10
    assert req.offset == 0
    assert req.course_id is None


def test_search_request_semantic_mode():
    from api.schemas.search import SearchRequest

    req = SearchRequest(q="neural networks", mode="semantic", limit=5)
    assert req.mode == "semantic"
    assert req.limit == 5


def test_search_result_structure():
    import uuid
    from api.schemas.search import SearchResult

    result = SearchResult(
        scene_id=uuid.uuid4(),
        lecture_id=uuid.uuid4(),
        lecture_title="Introduction to AI",
        chapter_title="Chapter 1",
        course_name="AI Fundamentals",
        timestamp_start=10.5,
        timestamp_end=45.2,
        transcript="Machine learning is a subset of AI",
        ocr_text=None,
        keyframe_url="http://minio/frames/scene.jpg",
        score=0.95,
    )

    assert result.lecture_title == "Introduction to AI"
    assert result.score == 0.95
    assert result.transcript is not None


def test_search_response_structure():
    import uuid
    from api.schemas.search import SearchResponse, SearchResult

    response = SearchResponse(
        results=[
            SearchResult(
                scene_id=uuid.uuid4(),
                lecture_id=uuid.uuid4(),
                lecture_title="Test Lecture",
                chapter_title="Test Chapter",
                course_name="Test Course",
                timestamp_start=0.0,
                timestamp_end=10.0,
                transcript="Test transcript",
                ocr_text=None,
                keyframe_url=None,
                score=0.8,
            )
        ],
        total=1,
        query="test query",
        mode="keyword",
    )

    assert len(response.results) == 1
    assert response.total == 1
    assert response.query == "test query"
    assert response.mode == "keyword"


@pytest.mark.asyncio
async def test_keyword_search_uses_fts():
    from api.schemas.search import SearchRequest

    request = SearchRequest(q="machine learning introduction", mode="keyword", limit=5)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("api.routers.search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            minio_public_url="http://localhost:9000",
            minio_bucket_frames="frames",
        )

        from api.routers.search import _keyword_search

        response = await _keyword_search(request, mock_db, mock_settings.return_value)

    assert response.mode == "keyword"
    assert response.query == "machine learning introduction"
    assert response.results == []
    assert response.total == 0

    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args
    sql_str = str(call_args[0][0])
    assert "plainto_tsquery" in sql_str or "fts_vector" in sql_str


@pytest.mark.asyncio
async def test_semantic_search_uses_vector_ops():
    from api.schemas.search import SearchRequest

    request = SearchRequest(q="convolution neural networks", mode="semantic", limit=3)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 1024)

    with (
        patch("api.routers.search.get_settings") as mock_settings,
        patch("api.routers.search._semantic_search") as mock_sem,
    ):
        from api.schemas.search import SearchResponse

        mock_sem.return_value = SearchResponse(
            results=[],
            total=0,
            query=request.q,
            mode="semantic",
        )
        mock_settings.return_value = MagicMock(
            minio_public_url="http://localhost:9000",
            minio_bucket_frames="frames",
        )

        response = await mock_sem(request, mock_db, mock_settings.return_value)

    assert response.mode == "semantic"
    assert response.query == "convolution neural networks"
