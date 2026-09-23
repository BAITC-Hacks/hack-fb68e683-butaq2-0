from app.evaluate_dataset import prediction, score


def test_predictions_preserve_multi_intent_order_and_system_intents():
    assert prediction({"scenario_id": "SC27", "pending_scenario_ids": ["SC04", "SC27"]}) == ["SC27", "SC04"]
    assert prediction({"reason": "out_of_scope: loans"}) == ["SYS_OUT_OF_SCOPE"]
    assert prediction({"reason": "goodbye: thanks"}) == ["SYS_GOODBYE"]
    assert prediction({"action": "clarify"}) == ["SYS_UNCLEAR"]


def test_metrics_count_missing_predictions_as_errors_and_split_languages():
    utterances = [
        {"id": "A", "expected": ["SC01"], "lang": "ru", "type": "single"},
        {"id": "B", "expected": ["SC27", "SC04"], "lang": "mixed", "type": "multi_intent"},
        {"id": "C", "expected": ["SC21"], "lang": "kk", "type": "single"},
    ]
    report = score(utterances, {"A": ["SC01"], "B": ["SC27"]})
    assert report["groups"]["all"]["primary_accuracy"] == 2 / 3
    assert report["groups"]["all"]["full_match"] == 1 / 3
    assert report["groups"]["lang=kk"]["primary_accuracy"] == 0
    assert report["multi_intent_recall"] == 0.5
