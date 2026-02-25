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


if __name__ == "__main__":
    unittest.main()
