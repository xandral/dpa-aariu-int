"""Analysis pipeline: text diff → embedding similarity → LLM classification."""

import json
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from app.config import LLM_MODELS, settings
from app.models import CheckStatus
from app.utils.openai_client import openai_client

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Output of the full analysis funnel for a single check."""

    status: CheckStatus
    diff_percentage: float
    similarity_score: float | None
    llm_analysis: dict | None
    check_embedding: list[float] | None = None


@dataclass
class Thresholds:
    """Analysis thresholds for a specific URL."""

    diff_ok: float
    diff_alert: float
    cosine_ok: float
    cosine_alert: float


def compute_diff(baseline_text: str, check_text: str) -> float:
    """Return the textual difference between two strings as a percentage (0-100).

    Uses difflib.SequenceMatcher which finds the longest common subsequences.
    0.0 means identical, 100.0 means completely different.
    """
    if not baseline_text and not check_text:
        return 0.0
    if not baseline_text or not check_text:
        return 100.0

    ratio = SequenceMatcher(None, baseline_text, check_text).ratio()
    return round((1.0 - ratio) * 100, 2)


def compute_cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Return cosine similarity between two embedding vectors (0-1).

    1.0 means identical direction (same meaning), 0.0 means orthogonal.
    """
    a = np.array(vec1, dtype=np.float64)
    b = np.array(vec2, dtype=np.float64)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def compute_embedding(text: str, model: str) -> list[float]:
    """Generate an embedding vector for the given text using OpenAI."""
    response = openai_client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def _build_llm_chunks(baseline: str, check: str, llm_model: str = "") -> list[tuple[str, str]]:
    """Split baseline and check into focused (baseline_chunk, check_chunk) pairs.

    Uses difflib opcodes to locate changed regions, adds context padding, merges
    nearby blocks, then applies parallel slicing on any block that exceeds the
    model's chunk limit.  Only the changed sections are sent to the LLM.

    max_chars is read from LLM_MODELS registry (chunk_size_chars) so the window
    size automatically adapts to the selected model.  Falls back to
    settings.llm_chunk_max_chars when the model is unknown.
    """
    ctx = settings.llm_context_chars
    gap = settings.llm_merge_gap_chars
    max_chars = LLM_MODELS.get(llm_model, {}).get("chunk_size_chars", settings.llm_chunk_max_chars)
    overlap = settings.llm_chunk_overlap_chars

    opcodes = SequenceMatcher(None, baseline, check).get_opcodes()
    changed = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in opcodes if tag != "equal"]

    if not changed:
        # Texts are identical — no chunk needed; caller should not reach level 3.
        return [(baseline[:max_chars], check[:max_chars])]

    # Add context padding around each changed block.
    regions: list[tuple[int, int, int, int]] = []
    for i1, i2, j1, j2 in changed:
        regions.append((
            max(0, i1 - ctx), min(len(baseline), i2 + ctx),
            max(0, j1 - ctx), min(len(check), j2 + ctx),
        ))

    # Merge blocks whose baseline windows are closer than merge_gap_chars.
    merged: list[tuple[int, int, int, int]] = [regions[0]]
    for b_s, b_e, c_s, c_e in regions[1:]:
        pb_s, pb_e, pc_s, pc_e = merged[-1]
        if b_s - pb_e < gap:
            merged[-1] = (pb_s, max(pb_e, b_e), pc_s, max(pc_e, c_e))
        else:
            merged.append((b_s, b_e, c_s, c_e))

    # Build final chunks; use parallel slicing on oversized merged blocks.
    # half = max window size per side so that len(b)+len(c) <= max_chars always.
    # step = half - overlap to keep context continuity between adjacent windows.
    half = max_chars // 2
    step = max(1, half - overlap)

    chunks: list[tuple[str, str]] = []
    for b_s, b_e, c_s, c_e in merged:
        b_sec = baseline[b_s:b_e]
        c_sec = check[c_s:c_e]
        if len(b_sec) + len(c_sec) <= max_chars:
            chunks.append((b_sec, c_sec))
        else:
            # Parallel slicing: same character offset applied to both sides so the
            # LLM always compares the exact same position in baseline vs check.
            max_len = max(len(b_sec), len(c_sec))
            offset = 0
            while offset < max_len:
                b_win = b_sec[offset : offset + half]
                c_win = c_sec[offset : offset + half]
                if b_win or c_win:
                    chunks.append((b_win, c_win))
                offset += step

    return chunks or [(baseline[:max_chars], check[:max_chars])]


def llm_classify(baseline_text: str, check_text: str, model: str) -> dict:
    """Ask an LLM to classify whether a changed section is legitimate or suspicious.

    Expects pre-chunked inputs from _build_llm_chunks — no internal truncation.
    Returns a dict with keys:
    - verdict: "OK" | "ALERT"
    - reason: short explanation
    """
    prompt = (
        "You are a web security analyst. Compare the following sections of a web page "
        "(before and after a change) and determine if the change looks like a legitimate "
        "content update or a potential defacement attack.\n\n"
        f"BASELINE:\n{baseline_text}\n\n"
        f"CURRENT:\n{check_text}\n\n"
        "Reply with a JSON object with two fields:\n"
        '- "verdict": either "OK" (legitimate change) or "ALERT" (suspicious/defacement)\n'
        '- "reason": a one-sentence explanation\n'
        "Reply with JSON only, no markdown."
    )

    response = openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def analyze(
    baseline_text: str,
    baseline_embedding: list[float] | None,
    check_text: str,
    thresholds: Thresholds,
    embedding_model: str,
    llm_model: str,
) -> AnalysisResult:
    """Run the full 3-level analysis funnel and return a result.

    Level 1 — text diff (free, instant):
        - diff < diff_ok  → OK
        - diff > diff_alert → ALERT

    Level 2 — embedding cosine similarity (cheap, fast):
        - cosine > cosine_ok → OK
        - cosine < cosine_alert → ALERT

    Level 3 — LLM classification (only for ambiguous cases):
        - returns OK or ALERT based on LLM verdict
    """
    # Level 1: text diff
    diff_pct = compute_diff(baseline_text, check_text)
    logger.debug("Diff: %.2f%%", diff_pct)

    if diff_pct <= thresholds.diff_ok:
        return AnalysisResult(
            status=CheckStatus.OK,
            diff_percentage=diff_pct,
            similarity_score=None,
            llm_analysis=None,
        )

    if diff_pct >= thresholds.diff_alert:
        return AnalysisResult(
            status=CheckStatus.ALERT,
            diff_percentage=diff_pct,
            similarity_score=None,
            llm_analysis=None,
        )

    # Level 2: embedding cosine similarity
    embedding = compute_embedding(check_text, embedding_model)
    similarity: float | None = None

    if baseline_embedding:
        similarity = compute_cosine_similarity(baseline_embedding, embedding)
        logger.debug("Cosine similarity: %.4f", similarity)

        if similarity >= thresholds.cosine_ok:
            return AnalysisResult(
                status=CheckStatus.OK,
                diff_percentage=diff_pct,
                similarity_score=similarity,
                llm_analysis=None,
                check_embedding=embedding,
            )

        if similarity <= thresholds.cosine_alert:
            return AnalysisResult(
                status=CheckStatus.ALERT,
                diff_percentage=diff_pct,
                similarity_score=similarity,
                llm_analysis=None,
                check_embedding=embedding,
            )

    # Level 3: LLM classification on diff-guided chunks (fail-fast on first ALERT)
    chunks = _build_llm_chunks(baseline_text, check_text, llm_model)
    logger.debug("Ambiguous case — calling LLM on %d chunk(s)", len(chunks))

    verdict = "OK"
    reasons: list[str] = []
    for b_chunk, c_chunk in chunks:
        result = llm_classify(b_chunk, c_chunk, llm_model)
        reasons.append(result.get("reason", ""))
        if result.get("verdict") == "ALERT":
            verdict = "ALERT"
            break  # fail-fast: one ALERT is enough

    llm_result = {"verdict": verdict, "reason": "; ".join(r for r in reasons if r)}
    status = CheckStatus.ALERT if verdict == "ALERT" else CheckStatus.CHANGED

    return AnalysisResult(
        status=status,
        diff_percentage=diff_pct,
        similarity_score=similarity,
        llm_analysis=llm_result,
        check_embedding=embedding,
    )
