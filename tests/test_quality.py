from bangla_ocr.models import OCRCandidate
from bangla_ocr.scoring import decide_page, score_candidates


SELECTION = {
    "minimum_candidate_score": 0.52,
    "minimum_engine_confidence": 0.72,
    "maximum_candidate_disagreement": 0.28,
    "minimum_render_width": 900,
    "minimum_render_height": 1200,
    "minimum_sharpness_laplacian_variance": 45.0,
}
CLASSIFICATION = {
    "automatic_story_threshold": 0.66,
    "minimum_story_bengali_characters": 180,
    "front_matter_scan_pages": 12,
}
PROSE = (
    "কিশোর জানালার পাশে দাঁড়িয়ে ছিল। বাইরে ঝড়ের শব্দ শোনা যাচ্ছে। "
    "মুসা বলল, “এখন আমাদের কী করা উচিত?” রবিন কোনো উত্তর দিল না। "
) * 10


def _decision(metrics: dict[str, float | int]):
    candidates, disagreement = score_candidates(
        [
            OCRCandidate(
                engine="surya",
                variant="original",
                text=PROSE,
                confidence=0.98,
            )
        ],
        SELECTION,
    )
    return decide_page(
        candidates,
        disagreement,
        20,
        100,
        SELECTION,
        CLASSIFICATION,
        metrics,
    )


def test_low_resolution_overrides_high_ocr_confidence():
    decision = _decision(
        {
            "width": 570,
            "height": 840,
            "sharpness_laplacian_variance": 300.0,
        }
    )
    assert decision.status == "needs_review"
    assert any("resolution is low" in reason for reason in decision.reasons)


def test_good_scan_can_remain_draft():
    decision = _decision(
        {
            "width": 1500,
            "height": 2300,
            "sharpness_laplacian_variance": 300.0,
        }
    )
    assert decision.status == "draft"
    assert decision.page_role == "body_text"


def test_blurred_scan_requires_review():
    decision = _decision(
        {
            "width": 1500,
            "height": 2300,
            "sharpness_laplacian_variance": 12.0,
        }
    )
    assert decision.status == "needs_review"
    assert any("appears blurred" in reason for reason in decision.reasons)
