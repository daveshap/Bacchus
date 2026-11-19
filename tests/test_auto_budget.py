import json
from io import BytesIO
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest import TestCase, mock
from urllib.error import URLError

from bacchus import budget, planning, providers


MESSAGES = "https://api.anthropic.com/v1/messages"
RESPONSES = "https://api.openai.com/v1/responses"
IMAGES = "https://api.openai.com/v1/images/edits"
MODEL = "custom-model"
RATES = {MODEL: {"inputUsdPerMillion": 10, "outputUsdPerMillion": 20}}


class Reply(BytesIO):
    headers = {"request-id": "request-test"}

    def getcode(self):
        return 200


def stop_worker_after_admission(folder, limits):
    with budget.scope(folder, limits):
        budget.run("Anthropic", MESSAGES, {"model": MODEL, "max_tokens": 100}, lambda: 5, lambda: os._exit(23))


class AutoBudgetTests(TestCase):
    def setUp(self):
        tmp = self.enterContext(TemporaryDirectory())
        self.folder = Path(tmp)
        self.open = self.enterContext(mock.patch.object(providers, "build_opener")).return_value.open
        self.enterContext(mock.patch.object(providers.settings, "api_log_directory", return_value=self.folder / "logs"))
        self.body = {"model": MODEL, "messages": [], "max_tokens": 100}

    def reply(self, *items):
        self.open.side_effect = [Reply(json.dumps(item).encode()) if isinstance(item, dict) else item for item in items]

    def send(self):
        return providers.post_json(MESSAGES, self.body, {}, "Anthropic")

    def test_two_reviewer_calls_share_a_token_and_dollar_limit(self):
        limits = {"maxTokens": 400, "maxUsd": 0.0052, "rates": RATES}
        usage = {"input_tokens": 12, "cache_read_input_tokens": 3, "output_tokens": 15}
        self.reply({"input_tokens": 25}, {"usage": usage}, {"input_tokens": 25})
        with budget.scope(self.folder, limits):
            self.send()
            self.assertEqual(budget.summary(self.folder)["tokens"], 30)
            self.assertEqual(budget.summary(self.folder)["usd"], 0.00045)
            with self.assertRaisesRegex(budget.BudgetError, "token limit"):
                self.send()
        sent = [c.args[0].full_url for c in self.open.call_args_list]
        self.assertEqual(sent, [MESSAGES+"/count_tokens", MESSAGES, MESSAGES+"/count_tokens"])
        self.assertEqual(budget.summary(self.folder)["calls"], 1)

    def test_api_failure_retains_reservation_after_restart(self):
        limits = {"maxTokens": 399, "rates": RATES}
        self.reply({"input_tokens": 5}, URLError("offline"), {"input_tokens": 5})
        with budget.scope(self.folder, limits), self.assertRaises(providers.ProviderError):
            self.send()
        with budget.scope(self.folder, limits), self.assertRaisesRegex(budget.BudgetError, "token limit"):
            self.send()
        held = budget.summary(self.folder)
        self.assertEqual((held["heldCalls"], held["tokens"], held["usd"]), (1, 361, 0.00461))

    def test_openai_and_anthropic_stream_share_ledger(self):
        events = [{"type": "message_start", "message": {"model": MODEL, "id": "m", "usage": {"input_tokens": 5}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
                  {"type": "message_stop"}]
        sse = Reply(b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events))
        self.reply({"input_tokens": 5}, sse, {"input_tokens": 10},
                   {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}})
        with budget.scope(self.folder, {"maxUsd": 1, "rates": RATES}):
            result, _ = providers.post_anthropic_stream(MESSAGES, self.body, {})
            providers.post_json(RESPONSES, {"model": MODEL, "max_output_tokens": 100, "input": "test"}, {}, "OpenAI")
        urls = [c.args[0].full_url for c in self.open.call_args_list]
        self.assertEqual(urls, [MESSAGES+"/count_tokens", MESSAGES, RESPONSES+"/input_tokens", RESPONSES])
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(budget.summary(self.folder)["tokens"], 23)
        self.assertEqual(budget.summary(self.folder)["usd"], 0.00031)

    def test_dollar_limit_and_missing_model_rate_block_paid_inference(self):
        self.reply({"input_tokens": 300})
        with budget.scope(self.folder, {"maxUsd": 0.001, "rates": RATES}):
            with self.assertRaisesRegex(budget.BudgetError, "spending limit"):
                self.send()
        with budget.scope(self.folder, {"maxUsd": 1}):
            with self.assertRaisesRegex(budget.BudgetError, "USD rates"):
                self.send()
        self.assertEqual([c.args[0].full_url for c in self.open.call_args_list], [MESSAGES+"/count_tokens"])

    def test_image_cost_is_bounded_per_request_and_zero_is_a_hard_stop(self):
        rate = {MODEL: {"imageMaxUsd": 0.40, "imageMaxTokens": 5000}}
        limits = {"maxUsd": 0.70, "maxTokens": 10_000, "rates": rate}
        self.reply({"usage": {"total_tokens": 2000}})
        body = {"model": MODEL, "n": 1}
        with budget.scope(self.folder, limits):
            providers.post_json(IMAGES, body, {}, "OpenAI")
            with self.assertRaisesRegex(budget.BudgetError, "spending limit"):
                providers.post_json(IMAGES, body, {}, "OpenAI")
        with budget.scope(self.folder, {"maxTokens": 0}):
            with self.assertRaisesRegex(budget.BudgetError, "zero Auto budget"):
                self.send()
        with budget.scope(self.folder, {"maxTokens": 20000}):
            with self.assertRaisesRegex(budget.BudgetError, "maximum tokens"):
                providers.post_json(IMAGES, body, {}, "OpenAI")
        self.assertEqual(self.open.call_count, 1)
        self.assertEqual(budget.summary(self.folder)["usd"], 0.4)

    def test_unpriced_history_counted_when_dollar_cap_is_enabled_later(self):
        self.reply({"usage": {"input_tokens": 60, "output_tokens": 15}})
        with budget.scope(self.folder):
            self.send()
        self.assertEqual(budget.summary(self.folder)["unknownUsdCalls"], 1)
        self.assertEqual(budget.summary(self.folder, {"rates": RATES})["usd"], 0.0009)

    def test_rejects_corrupt_ledger_and_bad_limits(self):
        with self.assertRaises(budget.BudgetError):
            budget.validate_limits({"maxTokens": True})
        with self.assertRaises(budget.BudgetError):
            budget.validate_limits({"maxUsd": float("nan")})
        with self.assertRaises(budget.BudgetError):
            budget.validate_limits({"rates": {MODEL: {"outputUsdPerMillion": -3}}})
        p = self.folder / "records"
        p.mkdir()
        (p / "auto-usage.sqlite3").write_bytes(b"truncated budget data")
        with budget.scope(self.folder), self.assertRaisesRegex(budget.BudgetError, "usage ledger"):
            self.send()
        self.open.assert_not_called()

    def test_chunk_planning_uses_stream_at_default_token_floor(self):
        response = {"id": "m", "model": MODEL, "stop_reason": "end_turn", "content": [{"type": "text", "text": '{"scenes":[]}'}]}
        with (mock.patch.object(planning, "provider_value", return_value="test-key"),
              mock.patch.object(planning, "post_anthropic_stream", return_value=(response, "r")) as stream):
            self.assertEqual(planning.request_plan({"model": MODEL, "max_tokens": planning.OUTPUT_TOKENS}, stream_large=True)[0], [])
        self.assertEqual(stream.call_count, 1)

    def test_counting_a_vision_request_forwards_image_and_schema_to_its_provider(self):
        examples = [(MESSAGES, "Anthropic", {"messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}}]}],
            "system": "look at this proof", "output_config": {"format": {"type": "json_schema", "schema": {}}},
            "max_tokens": 20}, {"max_tokens"}),
            (RESPONSES, "OpenAI", {"input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8=", "detail": "high"}]}],
                "instructions": "inspect frame", "text": {"format": {"type": "json_schema"}},
                "max_output_tokens": 20, "store": False}, {"max_output_tokens", "store"})]
        for url, provider, fields, omitted in examples:
            with self.subTest(provider=provider):
                self.reply({"input_tokens": 10_000})
                payload = {**fields, "model": MODEL}
                with budget.scope(self.folder, {"maxTokens": 5000}), self.assertRaises(budget.BudgetError):
                    providers.post_json(url, payload, {}, provider)
                self.assertEqual(json.loads(self.open.call_args.args[0].data),
                                 {k: v for k, v in payload.items() if k not in omitted})
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual(budget.summary(self.folder)["calls"], 0)

    def test_concurrent_calls_cannot_reuse_the_same_reservation(self):
        entered, release = Event(), Event()
        errors = []
        limits = {"maxTokens": 400}
        def wait_for_release():
            entered.set()
            if not release.wait(10):
                raise AssertionError("Test sender did not resume")
            return {"usage": {"input_tokens": 4, "output_tokens": 4}}, "paid"
        def submit():
            try:
                with budget.scope(self.folder, limits):
                    budget.run("Anthropic", MESSAGES, self.body, lambda: 5, wait_for_release)
            except Exception as error:
                errors.append(error)
        thread = Thread(target=submit)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(budget.summary(self.folder)["tokens"], 361)
            with budget.scope(self.folder, limits), self.assertRaisesRegex(budget.BudgetError, "token limit"):
                budget.run("Anthropic", MESSAGES, self.body, lambda: 5, mock.Mock(side_effect=AssertionError))
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(errors)
        self.assertEqual(budget.summary(self.folder)["tokens"], 8)

    def test_abrupt_process_death_leaves_committed_hold(self):
        limits = {"maxTokens": 400, "maxUsd": 0.005, "rates": RATES}
        worker = multiprocessing.get_context("spawn").Process(
            target=stop_worker_after_admission, args=(str(self.folder), limits))
        worker.start()
        worker.join(10)
        try:
            self.assertEqual(worker.exitcode, 23)
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            worker.close()
        with budget.scope(self.folder, limits), self.assertRaises(budget.BudgetError):
            budget.run("Anthropic", MESSAGES, self.body, lambda: 5, mock.Mock(side_effect=AssertionError))
        self.assertEqual(budget.summary(self.folder)["usd"], 0.00461)

    def test_aborted_stream_and_missing_final_usage_keep_the_full_hold(self):
        start = {"type": "message_start", "message": {"id": "m", "model": MODEL, "usage": {"input_tokens": 5, "output_tokens": 1}}}
        delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
        for events, completes in (([start, delta], False), ([start, delta, {"type": "message_stop"}], True)):
            raw = Reply(b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events))
            self.reply({"input_tokens": 5}, raw)
            with budget.scope(self.folder, {"maxTokens": 800}):
                if completes:
                    providers.post_anthropic_stream(MESSAGES, self.body, {})
                else:
                    with self.assertRaises(providers.ProviderError):
                        providers.post_anthropic_stream(MESSAGES, self.body, {})
        self.assertEqual(budget.summary(self.folder)["tokens"], 2*361)
        self.assertEqual(budget.summary(self.folder)["heldCalls"], 2)

    def test_count_provider_error_aborts_without_inference_or_hold(self):
        self.reply(URLError("count service failed"))
        with budget.scope(self.folder, {"maxTokens": 900}), self.assertRaises(providers.ProviderError):
            self.send()
        self.assertEqual(self.open.call_count, 1)
        self.assertEqual(self.open.call_args.args[0].full_url, MESSAGES+"/count_tokens")
        self.assertEqual(budget.summary(self.folder)["calls"], 0)

    def test_unpriced_uncapped_failed_call_blocks_later_capped_run(self):
        self.reply(URLError("provider may have received it"), {"input_tokens": 5})
        with budget.scope(self.folder), self.assertRaises(providers.ProviderError):
            self.send()
        with budget.scope(self.folder, {"maxUsd": 2, "rates": RATES}), self.assertRaisesRegex(budget.BudgetError, "earlier Auto call"):
            self.send()
        self.assertEqual(budget.summary(self.folder, {"rates": RATES})["unknownUsdCalls"], 1)
