"""Unit tests for the analysis pipeline."""

from unittest.mock import patch

import pytest

from app.models import CheckStatus
from app.services.analyzer import (
    Thresholds,
    _build_llm_chunks,
    analyze,
    compute_cosine_similarity,
    compute_diff,
)

DEFAULT_THRESHOLDS = Thresholds(
    diff_ok=5.0,
    diff_alert=50.0,
    cosine_ok=0.95,
    cosine_alert=0.5,
)
DEFAULT_MODELS = {"embedding_model": "text-embedding-3-small", "llm_model": "gpt-4o-mini"}


# ---------------------------------------------------------------------------
# _build_llm_chunks
# ---------------------------------------------------------------------------


def test_build_chunks_identical_texts():
    """Identical texts produce one chunk (caller shouldn't reach level 3, but safe)."""
    chunks = _build_llm_chunks("hello world", "hello world")
    assert len(chunks) == 1


def test_build_chunks_small_change():
    """A small change produces exactly one chunk containing the modified area."""
    baseline = "AAA " * 100 + "old word" + " ZZZ" * 100
    check = "AAA " * 100 + "new word" + " ZZZ" * 100
    chunks = _build_llm_chunks(baseline, check)
    assert len(chunks) >= 1
    combined = " ".join(b + c for b, c in chunks)
    assert "old word" in combined
    assert "new word" in combined


def test_build_chunks_splits_long_block(monkeypatch):
    """A single diff block larger than llm_chunk_max_chars is split into multiple windows."""
    import app.services.analyzer as az

    monkeypatch.setattr(az.settings, "llm_chunk_max_chars", 20)
    monkeypatch.setattr(az.settings, "llm_context_chars", 0)
    monkeypatch.setattr(az.settings, "llm_merge_gap_chars", 5)
    monkeypatch.setattr(az.settings, "llm_chunk_overlap_chars", 2)

    baseline = "A" * 100
    check = "B" * 100
    chunks = _build_llm_chunks(baseline, check)
    assert len(chunks) > 1
    # Every chunk must respect the half-size limit
    half = az.settings.llm_chunk_max_chars // 2
    for b, c in chunks:
        assert len(b) <= half
        assert len(c) <= half


def test_build_chunks_merges_nearby_blocks(monkeypatch):
    """Two diff blocks close together are merged into one chunk."""
    import app.services.analyzer as az

    monkeypatch.setattr(az.settings, "llm_merge_gap_chars", 1000)
    monkeypatch.setattr(az.settings, "llm_context_chars", 10)
    monkeypatch.setattr(az.settings, "llm_chunk_max_chars", 99999)
    monkeypatch.setattr(az.settings, "llm_chunk_overlap_chars", 50)

    baseline = "start " + "X" * 5 + " middle " + "X" * 5 + " end"
    check = "start " + "Y" * 5 + " middle " + "Y" * 5 + " end"
    chunks = _build_llm_chunks(baseline, check)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------


def test_diff_identical_texts():
    assert compute_diff("hello world", "hello world") == 0.0


def test_diff_completely_different():
    result = compute_diff("hello world", "HACKED BY ANONYMOUS")
    assert result > 70.0


def test_diff_empty_both():
    assert compute_diff("", "") == 0.0


def test_diff_one_empty():
    assert compute_diff("some content", "") == 100.0


def test_diff_small_change():
    result = compute_diff("Hello world", "Hello World")
    assert result < 10.0


# ---------------------------------------------------------------------------
# compute_cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert compute_cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    assert compute_cosine_similarity(v1, v2) == pytest.approx(0.0)


def test_cosine_zero_vector():
    assert compute_cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_opposite_vectors():
    result = compute_cosine_similarity([1.0, 0.0], [-1.0, 0.0])
    assert result == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# analyze — decision funnel
# ---------------------------------------------------------------------------


def test_analyze_ok_by_diff():
    """Small diff should resolve at level 1 without calling OpenAI."""
    result = analyze(
        baseline_text="Hello world this is a page",
        baseline_embedding=None,
        check_text="Hello world this is a page",
        thresholds=DEFAULT_THRESHOLDS,
        **DEFAULT_MODELS,
    )
    assert result.status == CheckStatus.OK
    assert result.diff_percentage == 0.0
    assert result.similarity_score is None
    assert result.llm_analysis is None


def test_analyze_alert_by_diff():
    """Large diff should resolve at level 1 as ALERT."""
    result = analyze(
        baseline_text="Welcome to our company website. We sell great products.",
        baseline_embedding=None,
        check_text="HACKED BY ANONYMOUS. Your security is a joke. All your data belong to us.",
        thresholds=DEFAULT_THRESHOLDS,
        **DEFAULT_MODELS,
    )
    assert result.status == CheckStatus.ALERT
    assert result.diff_percentage > 50.0


def test_analyze_ok_by_cosine():
    """Ambiguous diff but high cosine similarity → OK without LLM."""
    baseline_vec = [1.0, 0.0, 0.0]
    check_vec = [0.999, 0.001, 0.0]

    with patch("app.services.analyzer.compute_diff", return_value=20.0):
        with patch("app.services.analyzer.compute_embedding", return_value=check_vec):
            with patch("app.services.analyzer.compute_cosine_similarity", return_value=0.98):
                result = analyze(
                    baseline_text="some baseline text",
                    baseline_embedding=baseline_vec,
                    check_text="some check text",
                    thresholds=DEFAULT_THRESHOLDS,
                    **DEFAULT_MODELS,
                )

    assert result.status == CheckStatus.OK
    assert result.llm_analysis is None


def test_analyze_alert_by_cosine():
    """Ambiguous diff but very low cosine → ALERT without LLM."""
    baseline_vec = [1.0, 0.0, 0.0]

    with patch("app.services.analyzer.compute_diff", return_value=20.0):
        with patch("app.services.analyzer.compute_embedding", return_value=[0.0, 1.0, 0.0]):
            with patch("app.services.analyzer.compute_cosine_similarity", return_value=0.1):
                result = analyze(
                    baseline_text="some baseline text",
                    baseline_embedding=baseline_vec,
                    check_text="some check text",
                    thresholds=DEFAULT_THRESHOLDS,
                    **DEFAULT_MODELS,
                )

    assert result.status == CheckStatus.ALERT
    assert result.llm_analysis is None


def test_analyze_ambiguous_calls_llm():
    """Ambiguous diff AND ambiguous cosine → LLM is called."""
    baseline_vec = [1.0, 0.0]

    with patch("app.services.analyzer.compute_diff", return_value=20.0):
        with patch("app.services.analyzer.compute_embedding", return_value=[0.8, 0.2]):
            with patch("app.services.analyzer.compute_cosine_similarity", return_value=0.75):
                with patch(
                    "app.services.analyzer.llm_classify",
                    return_value={"verdict": "ALERT", "reason": "Suspicious content injected"},
                ):
                    result = analyze(
                        baseline_text="some baseline text",
                        baseline_embedding=baseline_vec,
                        check_text="some check text",
                        thresholds=DEFAULT_THRESHOLDS,
                        **DEFAULT_MODELS,
                    )

    assert result.status == CheckStatus.ALERT
    assert result.llm_analysis is not None
    assert result.llm_analysis["verdict"] == "ALERT"
