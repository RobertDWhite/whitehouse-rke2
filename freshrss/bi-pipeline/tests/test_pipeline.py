import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "pipeline.py"
SPEC = importlib.util.spec_from_file_location("freshrss_bi_pipeline", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load pipeline module from {MODULE_PATH}")
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def test_merge_seen_ids_preserves_order_and_limit(self) -> None:
        merged = pipeline.merge_seen_ids(["a", "b", "c", "b"], ["d", "a", "e"], keep_seen=4)
        self.assertEqual(merged, ["b", "c", "d", "e"])

    def test_score_keyword_match_uses_token_boundaries(self) -> None:
        article = {"title": "Ramp launches updates", "summary": "", "url": "https://example.com"}
        event = {
            "entities": {"companies": [], "products": []},
            "urgency": 1,
            "confidence": 1.0,
            "signals": {
                "health_risk": 0.0,
                "cloud_spend_pressure": 0.0,
                "churn_risk": 0.0,
                "renewal_risk": 0.0,
            },
            "event_type": "other",
        }
        defaults = {
            "keyword_match": 10.0,
            "competitor_match": 0.0,
            "cloud_match": 0.0,
            "feed_match": 0.0,
            "domain_match": 0.0,
            "urgency": 0.0,
            "health_risk": 0.0,
            "cloud_spend_pressure": 0.0,
            "churn_risk": 0.0,
            "renewal_risk": 0.0,
        }

        score_partial, details_partial = pipeline.score_for_customer(
            article=article,
            event=event,
            customer={"keywords": ["ram"], "competitors": [], "cloud_keywords": []},
            defaults=defaults,
        )
        score_exact, details_exact = pipeline.score_for_customer(
            article=article,
            event=event,
            customer={"keywords": ["ramp"], "competitors": [], "cloud_keywords": []},
            defaults=defaults,
        )

        self.assertEqual(details_partial["keyword_hits"], 0)
        self.assertEqual(score_partial, 0.0)
        self.assertEqual(details_exact["keyword_hits"], 1)
        self.assertGreater(score_exact, 0.0)

    def test_write_outputs_includes_customers_without_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "20260225T000000Z"
            payload = {
                "run_at": "2026-02-25T00:00:00+00:00",
                "fetched_articles": 1,
                "new_articles": 1,
                "total_alerts": 1,
                "customer_alerts": {
                    "With Alerts": [
                        {
                            "article_title": "Title",
                            "score": 50.0,
                            "source": "Example",
                            "source_domain": "example.com",
                            "event_type": "other",
                            "urgency": 2,
                            "why_it_matters": "Impact",
                            "recommended_actions": ["Act"],
                            "url": "https://example.com/article",
                        }
                    ],
                    "Without Alerts": [],
                },
            }
            pipeline.write_outputs(
                run_dir=run_dir,
                run_payload=payload,
                top_n=3,
                customer_names=["With Alerts", "Without Alerts"],
            )
            digest = (run_dir / "digest.md").read_text(encoding="utf-8")
            self.assertIn("## Without Alerts", digest)
            self.assertIn("No alerts above threshold.", digest)

    def test_prune_output_runs_by_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            run_ids = [
                "20260225T000000Z",
                "20260225T003000Z",
                "20260225T010000Z",
                "20260225T013000Z",
                "20260225T020000Z",
            ]
            for run_id in run_ids:
                (base / run_id).mkdir(parents=True)
            stats = pipeline.prune_output_runs(base, retention_days=0, max_run_directories=2)
            remaining = sorted(p.name for p in base.iterdir() if p.is_dir())
        self.assertEqual(stats["removed_by_count"], 3)
        self.assertEqual(remaining, run_ids[-2:])

    def test_dynamic_threshold_uses_percentile_history(self) -> None:
        state = {
            "dynamic_thresholds": {
                "history": {
                    "Acme": [20, 35, 42, 48, 51, 63, 70, 74, 82, 88],
                }
            }
        }
        cfg = {
            "enabled": True,
            "percentile": 0.8,
            "blend_weight": 1.0,
            "min_history": 5,
            "clamp_min": 25,
            "clamp_max": 90,
        }
        threshold = pipeline.dynamic_threshold_for_customer("Acme", base_threshold=40.0, state=state, cfg=cfg)
        self.assertGreaterEqual(threshold, 70.0)
        self.assertLessEqual(threshold, 90.0)

    def test_feedback_quality_adjustment_penalizes_low_precision_source(self) -> None:
        state = {
            "feedback": {
                "stats": {
                    "by_customer_source": {"Acme|bad.example.com": {"positive": 1, "negative": 9, "neutral": 0}},
                    "by_source": {"bad.example.com": {"positive": 1, "negative": 9, "neutral": 0}},
                    "by_event_type": {"security_incident": {"positive": 8, "negative": 2, "neutral": 0}},
                }
            }
        }
        cfg = {"enabled": True, "min_samples_for_adjustment": 3, "max_adjustment": 0.25}
        multiplier, components = pipeline.feedback_quality_adjustment(
            customer_name="Acme",
            source_domain="bad.example.com",
            event_type="security_incident",
            feedback_state=state,
            feedback_cfg=cfg,
        )
        self.assertLess(multiplier, 1.0)
        self.assertIn("source", components)

    def test_story_signature_stable_for_same_event(self) -> None:
        article = {"title": "Vendor outage impacts cloud customers", "summary": "Major downtime event"}
        event = {"event_type": "outage_incident", "summary": "Cloud outage causes downtime"}
        sig1 = pipeline.stable_story_signature(article, event)
        sig2 = pipeline.stable_story_signature(article, event)
        self.assertEqual(sig1, sig2)
        self.assertEqual(pipeline.story_id_from_signature(sig1), pipeline.story_id_from_signature(sig2))


if __name__ == "__main__":
    unittest.main()
