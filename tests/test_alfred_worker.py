import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("alfred")
from autoqc.alfred_worker import infer


@pytest.fixture
def endpoint():
    requests = []
    state = {"status": 200, "content": json.dumps({
        "labels": {"bad_product": False, "bad_movement": True, "nudity": None},
        "evidence": {"explanation": "Visible discontinuity."},
    })}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, dict(self.headers), json.loads(
                self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps({"choices": [{"message": {"content": state["content"]}}]}).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def run(endpoint, *, mode="cosmos4fps", **sampling):
    return infer({
        "video_url": "https://autoqc.test/api/media/abc",
        "metadata": {"reference_image_urls": ["https://ref.test/front.jpg", "https://ref.test/back.jpg"],
                     "labels": {"bad_product": True}, "decision": "approved"},
    }, {"mode": mode, "prompt": "Inspect this product.", "worker_settings": {
        "endpoint": endpoint, "served_model": "test-model", **sampling,
    }})


def test_cosmos_uses_alfred_preserving_typed_media_and_priority(endpoint, monkeypatch):
    url, requests, _ = endpoint
    monkeypatch.setenv("AUTOQC_LLM_PRIORITY", "experimental")
    result = run(url + "/cosmos/v1", fps=4)
    path, headers, body = requests[0]
    assert path == "/cosmos/v1/chat/completions"
    assert headers["X-Drivetrain-Priority"] == "experimental"
    assert body["model"] == "test-model"
    assert body["media_io_kwargs"] == {"video": {"fps": 4}}
    assert "chat_template_kwargs" not in body
    assert [b["type"] for b in body["messages"][1]["content"]] == [
        "video_url", "image_url", "image_url", "text", "text"]
    assert "approved" not in json.dumps(body)
    assert result["labels"] == {"bad_product": False, "bad_movement": True, "nudity": None}
    assert result["evidence"]["inference_backend"] == "minivet_alfred"


def test_fixed_frames_do_not_gain_cosmos_default_fps(endpoint):
    url, requests, _ = endpoint
    result = run(url, num_frames=8)
    assert requests[0][2]["media_io_kwargs"] == {"video": {"num_frames": 8}}
    assert result["evidence"]["video_sampling"] == {"num_frames": 8, "source": "benchmark_snapshot"}


def test_qwen_preserves_sampling_handoff(endpoint):
    url, requests, _ = endpoint
    run(url, mode="qwenA3b4fps", fps=2)
    body = requests[0][2]
    assert body["mm_processor_kwargs"] == {"fps": 2, "do_sample_frames": False}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_gateway_failure_is_not_retried_in_alfred(endpoint):
    url, requests, state = endpoint
    state["status"] = 503
    with pytest.raises(RuntimeError, match="503"):
        run(url)
    assert len(requests) == 1


def test_missing_labels_are_errors_not_abstentions(endpoint):
    url, _, state = endpoint
    state["content"] = '{"labels":{"bad_product":false}}'
    with pytest.raises(ValueError, match="omitted"):
        run(url)
