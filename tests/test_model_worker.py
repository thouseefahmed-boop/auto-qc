import pytest

from autoqc.model_worker import MODEL_SERVICES, _messages, _prompt, _sampling_for


def test_default_sampling_is_model_specific():
    qwen_body, qwen_audit = _sampling_for("qwenA3b4fps", MODEL_SERVICES["qwenA3b4fps"])
    cosmos_body, cosmos_audit = _sampling_for("cosmos4fps", MODEL_SERVICES["cosmos4fps"])
    assert qwen_body == {
        "media_io_kwargs": {"video": {"fps": 2.0}},
        "mm_processor_kwargs": {"fps": 2.0, "do_sample_frames": False},
    }
    assert cosmos_body == {"media_io_kwargs": {"video": {"fps": 4.0}}}
    assert qwen_audit["fps"] == 2.0
    assert cosmos_audit["fps"] == 4.0


def test_sampling_override_and_mutual_exclusion(monkeypatch):
    monkeypatch.setenv("AUTOQC_QWENA3B4FPS_FPS", "1.5")
    body, audit = _sampling_for("qwenA3b4fps", MODEL_SERVICES["qwenA3b4fps"])
    assert body["mm_processor_kwargs"]["fps"] == 1.5
    assert audit == {"fps": 1.5, "source": "worker_environment"}
    monkeypatch.setenv("AUTOQC_QWENA3B4FPS_NUM_FRAMES", "8")
    with pytest.raises(ValueError, match="only one"):
        _sampling_for("qwenA3b4fps", MODEL_SERVICES["qwenA3b4fps"])


def test_prompt_contains_recipe_and_output_contract():
    prompt = _prompt({"prompt": "Inspect carefully.", "label_definitions": {"nudity": "Policy"}})
    assert "Inspect carefully." in prompt
    assert '"nudity": "Policy"' in prompt
    assert '"bad_product":true|false|null' in prompt


def test_messages_send_all_reference_images():
    messages = _messages(
        {
            "video_url": "https://example.test/video.mp4",
            "metadata": {
                "product_title": "Blue shirt",
                "reference_image_urls": [
                    "https://example.test/front.jpg",
                    "https://example.test/back.jpg",
                    "https://example.test/front.jpg",
                ],
            },
        },
        {"prompt": "Compare the product.", "label_definitions": {}},
    )
    content = messages[1]["content"]
    images = [block["image_url"]["url"] for block in content if block["type"] == "image_url"]
    assert images == ["https://example.test/front.jpg", "https://example.test/back.jpg"]
    assert "2 total" in content[-3]["text"]
