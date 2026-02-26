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

    def test_infer_customer_tier_uses_arr_ranges(self) -> None:
        customer = {"business_context": {"arr": 450000}}
        cfg = {
            "default_tier": "standard",
            "tiers": [
                {"name": "strategic", "min_arr": 400000, "score_multiplier": 1.1, "threshold_adjustment": -5},
                {"name": "growth", "min_arr": 200000, "max_arr": 399999, "score_multiplier": 1.05},
            ],
        }
        tier = pipeline.infer_customer_tier(customer, cfg)
        self.assertEqual(tier["name"], "strategic")
        self.assertGreater(tier["score_multiplier"], 1.0)

    def test_resolve_playbooks_merges_segment_and_default(self) -> None:
        customer = {"business_context": {"segment": "enterprise_healthcare"}}
        cfg = {
            "default": {"security_incident": ["Default Action"]},
            "by_segment": {"enterprise_healthcare": {"security_incident": ["Segment Action"]}},
        }
        actions = pipeline.resolve_playbooks(customer, "security_incident", cfg)
        self.assertIn("Segment Action", actions)
        self.assertIn("Default Action", actions)

    def test_score_for_customer_uses_business_context_signals(self) -> None:
        article = {
            "title": "AWS outage raises HIPAA and reliability concerns",
            "summary": "Cloud incident impacts healthcare workloads",
            "url": "https://example.com/news",
        }
        event = {
            "entities": {"companies": ["AWS"], "products": [], "vendors": ["AWS"]},
            "urgency": 4,
            "confidence": 0.9,
            "signals": {
                "health_risk": 0.7,
                "cloud_spend_pressure": 0.4,
                "churn_risk": 0.6,
                "renewal_risk": 0.5,
            },
            "impact_vectors": {"financial": 0.4},
            "event_type": "outage_incident",
        }
        customer = {
            "keywords": ["healthcare"],
            "competitors": ["datadog"],
            "cloud_keywords": ["aws"],
            "business_context": {
                "exec_priorities": ["hipaa", "reliability"],
                "stack_confirmed": ["aws"],
                "open_risks": ["outage"],
                "stage": "at_risk",
            },
        }
        defaults = {
            "keyword_match": 5,
            "competitor_match": 8,
            "cloud_match": 6,
            "feed_match": 0,
            "domain_match": 0,
            "context_match": 0,
            "urgency": 3,
            "health_risk": 10,
            "cloud_spend_pressure": 5,
            "churn_risk": 8,
            "renewal_risk": 8,
            "novelty": 3,
            "strategic_priority_match": 5,
            "stack_confirmed_match": 6,
            "stack_possible_match": 3,
            "open_risk_match": 4,
            "committee_priority_match": 4,
            "stage_boosts": {"at_risk": 6},
        }
        score, details = pipeline.score_for_customer(
            article=article,
            event=event,
            customer=customer,
            defaults=defaults,
            extra_context={"novelty": 0.5, "account_multiplier": 1.1},
        )
        self.assertGreater(score, 0.0)
        self.assertGreater(details["strategic_priority_hits"], 0)
        self.assertGreater(details["stack_confirmed_hits"], 0)

    def test_calc_account_heat_score_bands(self) -> None:
        customer = {"renewal_date": "2026-03-15", "business_context": {"open_risks": ["risk1", "risk2"]}}
        alerts = [{"score": 88.0}, {"score": 72.0}]
        watchlist = [{"score": 41.0}]
        heat = pipeline.calc_account_heat_score(customer, alerts, watchlist, competitor_pressure=12.0)
        self.assertIn(heat["band"], {"medium", "high"})
        self.assertGreaterEqual(heat["score"], 40.0)


if __name__ == "__main__":
    unittest.main()
