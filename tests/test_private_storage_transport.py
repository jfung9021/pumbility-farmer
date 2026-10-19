from __future__ import annotations

import gc
import hashlib
import json
import ssl
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, Mock, patch

import requests
from urllib3.exceptions import MaxRetryError, SSLError as UrllibSSLError

from private_storage_transport import PrivateStorageTransport, classify_tls_error
from pumbility_store import EXPECTED_PUMBILITY_MIGRATION, PumbilityArtifactStore


def response(status: int = 200, *, body: bytes = b"payload", retry_after: str | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body
    result._content_consumed = True
    if retry_after is not None:
        result.headers["Retry-After"] = retry_after
    return result


class PrivateStorageTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        self.clock = 0.0
        self.session_patch = patch("private_storage_transport.requests.Session", return_value=self.session)
        self.session_factory = self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.monotonic_patch = patch("private_storage_transport.time.monotonic", side_effect=lambda: self.clock)
        self.monotonic_patch.start()
        self.addCleanup(self.monotonic_patch.stop)
        self.sleep_patch = patch("private_storage_transport.time.sleep", side_effect=self.advance)
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.random_patch = patch("private_storage_transport.random.uniform", return_value=0.0)
        self.random_patch.start()
        self.addCleanup(self.random_patch.stop)
        self.event_patch = patch("private_storage_transport.tier_diagnostics.event")
        self.event = self.event_patch.start()
        self.addCleanup(self.event_patch.stop)
        self.transport = PrivateStorageTransport()

    def advance(self, seconds: float) -> None:
        self.clock += seconds

    def request(self, method: str = "GET", **kwargs):
        if method == "POST":
            kwargs = {"headers": {"x-upsert": "true", "Authorization": "private-secret"}, "data": b"payload", **kwargs}
        return self.transport.request(method, "https://storage.invalid/private-key", timeout=30.0, **kwargs)

    def test_same_thread_reuses_session_without_session_credentials(self) -> None:
        self.session.request.return_value = response()
        self.request("POST")
        self.request("POST")
        self.session_factory.assert_called_once_with()
        self.assertEqual(self.session.request.call_count, 2)
        self.session.headers.update.assert_not_called()
        self.event.assert_not_called()

    def test_concurrent_threads_have_separate_sessions(self) -> None:
        sessions = [Mock(), Mock()]
        for session in sessions:
            session.request.return_value = response()
        self.session_factory.side_effect = sessions
        barrier = threading.Barrier(2)

        def fetch(_: int) -> None:
            barrier.wait()
            self.request()
            self.request()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(fetch, range(2)))
        self.assertEqual(self.session_factory.call_count, 2)
        self.assertEqual([session.request.call_count for session in sessions], [2, 2])

    def test_sessions_close_when_transport_is_released(self) -> None:
        self.session.request.return_value = response()
        self.request()
        reference = weakref.ref(self.transport)
        del self.transport
        gc.collect()
        self.assertIsNone(reference())
        self.session.close.assert_called_once_with()

    def test_known_transient_errors_retry_with_identical_upload(self) -> None:
        eof = requests.exceptions.SSLError(MaxRetryError(None, "/private-key", UrllibSSLError(ssl.SSLEOFError(8, "EOF"))))
        for error in (eof, requests.exceptions.ConnectionError(ConnectionResetError(10054, "reset")),
                      requests.exceptions.ConnectTimeout("timeout"), requests.exceptions.ReadTimeout("timeout")):
            with self.subTest(error=type(error).__name__):
                self.session.request.reset_mock()
                self.event.reset_mock()
                self.session.request.side_effect = [error, response()]
                self.assertEqual(self.request("POST").status_code, 200)
                self.assertEqual(self.session.request.call_count, 2)
                calls = self.session.request.call_args_list
                self.assertEqual(calls[0].args, calls[1].args)
                self.assertEqual(calls[0].kwargs["headers"], calls[1].kwargs["headers"])
                self.assertIs(calls[0].kwargs["data"], calls[1].kwargs["data"])
                self.assertGreater(calls[0].kwargs["timeout"], calls[1].kwargs["timeout"])
                self.assertEqual(self.event.call_args.kwargs["attempt"], 2)
                self.assertEqual(self.event.call_args.kwargs["outcome"], "success")
                self.assertEqual(self.event.call_args.kwargs["exception_class"], type(error).__name__)
                self.assertEqual(self.event.call_args.kwargs["tls_category"], "eof" if error is eof else "none")

    def test_http_transients_retry_and_close_previous_response(self) -> None:
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                first = response(status)
                with patch.object(first, "close") as close:
                    self.session.request.reset_mock()
                    self.session.request.side_effect = [first, response()]
                    self.assertEqual(self.request().status_code, 200)
                    self.assertEqual(self.session.request.call_count, 2)
                    close.assert_called_once_with()

    def test_auth_policy_and_permanent_statuses_do_not_retry(self) -> None:
        for status in (400, 401, 403, 404, 409, 413, 422, 501):
            with self.subTest(status=status):
                self.session.request.reset_mock()
                self.session.request.side_effect = [response(status)]
                self.assertEqual(self.request().status_code, status)
                self.assertEqual(self.session.request.call_count, 1)

    def test_certificate_and_unknown_ssl_errors_do_not_retry(self) -> None:
        certificate = ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED private-secret")
        wrapped = requests.exceptions.SSLError(MaxRetryError(None, "/private-key", UrllibSSLError(certificate)))
        for error, category in ((wrapped, "certificate"), (requests.exceptions.SSLError("unknown private-secret"), "ssl_other")):
            with self.subTest(category=category):
                self.session.request.reset_mock()
                self.session.request.side_effect = error
                with self.assertRaises(requests.exceptions.SSLError):
                    self.request()
                self.assertEqual(self.session.request.call_count, 1)
                fields = self.event.call_args.kwargs
                self.assertEqual(fields["tls_category"], category)
                self.assertNotIn("private-secret", json.dumps(fields))
                self.assertNotIn("private-key", json.dumps(fields))

    def test_wrapped_eof_text_is_classified_without_treating_other_tls_as_transient(self) -> None:
        self.assertEqual(classify_tls_error(requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING")), "eof")
        self.assertEqual(classify_tls_error(requests.exceptions.SSLError("TLSV1_ALERT_UNKNOWN_CA")), "ssl_other")

    def test_retry_count_is_bounded_and_final_error_is_raised(self) -> None:
        error = requests.exceptions.ConnectionError(ConnectionResetError("reset"))
        self.session.request.side_effect = error
        with self.assertRaises(requests.exceptions.ConnectionError) as caught:
            self.request()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.session.request.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.event.call_args.kwargs["attempt"], 3)

    def test_http_failure_after_three_attempts_remains_failure(self) -> None:
        self.session.request.side_effect = [response(503), response(503), response(503)]
        result = self.request()
        with self.assertRaises(requests.exceptions.HTTPError):
            result.raise_for_status()
        self.assertEqual(self.session.request.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.event.call_args.kwargs["outcome"], "failed")
        self.assertEqual(self.event.call_args.kwargs["status_code"], 503)

    def test_elapsed_timeout_does_not_start_another_request(self) -> None:
        def timed_out(*_, **__):
            self.advance(30.0)
            raise requests.exceptions.ReadTimeout("private-secret")

        self.session.request.side_effect = timed_out
        with self.assertRaises(requests.exceptions.ReadTimeout):
            self.request()
        self.assertEqual(self.session.request.call_count, 1)
        self.sleep.assert_not_called()

    def test_retry_after_outside_budget_is_not_ignored(self) -> None:
        self.session.request.side_effect = [response(429, retry_after="120")]
        self.assertEqual(self.request().status_code, 429)
        self.assertEqual(self.session.request.call_count, 1)
        self.sleep.assert_not_called()

    def test_backoff_overrun_does_not_start_request_after_budget(self) -> None:
        for result in (requests.exceptions.ReadTimeout("private-secret"), response(503)):
            with self.subTest(result=type(result).__name__):
                self.clock = 0.0
                self.session.request.reset_mock()
                self.session.request.side_effect = [result, response()]
                self.sleep.side_effect = lambda _: self.advance(31.0)
                if isinstance(result, Exception):
                    with self.assertRaises(requests.exceptions.ReadTimeout) as caught:
                        self.request()
                    self.assertIs(caught.exception, result)
                else:
                    self.assertIs(self.request(), result)
                self.assertEqual(self.session.request.call_count, 1)
                self.assertEqual(self.event.call_args.kwargs["outcome"], "failed")
                self.assertEqual(self.event.call_args.kwargs["attempt"], 1)

    def test_short_retry_after_is_honored(self) -> None:
        self.session.request.side_effect = [response(503, retry_after="1"), response()]
        self.request()
        self.sleep.assert_called_once_with(1.0)

    def test_non_repeatable_upload_and_program_errors_are_not_retried(self) -> None:
        with self.assertRaises(ValueError):
            self.transport.request("POST", "https://storage.invalid/", timeout=30, data=b"payload")
        self.session.request.assert_not_called()
        self.session.request.side_effect = ValueError("invalid request")
        with self.assertRaises(ValueError):
            self.request()
        self.assertEqual(self.session.request.call_count, 1)


class BinaryStoreTransportIntegrationTests(unittest.TestCase):
    def store(self) -> PumbilityArtifactStore:
        return PumbilityArtifactStore(database_url="postgresql://localhost/local", supabase_url="https://storage.invalid",
                                     service_key="private-secret", bucket="private-bucket")

    def test_upload_metadata_is_written_only_after_successful_http(self) -> None:
        store = self.store()
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch("pumbility_store._connect", return_value=connection) as connect, patch("pumbility_store._assert_schema"):
            def upload(*args, **kwargs):
                connect.assert_not_called()
                self.assertEqual(args[0], "POST")
                self.assertEqual(kwargs["headers"]["x-upsert"], "true")
                return response()

            with patch.object(store._binary_transport, "request", side_effect=upload):
                store.put_bytes("private-key", b"payload", content_type="application/octet-stream")
        self.assertEqual(cursor.execute.call_args.args[1][-2:], (hashlib.sha256(b"payload").hexdigest(), 7))

    def test_exhausted_or_permanent_upload_failure_never_writes_metadata(self) -> None:
        store = self.store()
        for result in (response(503), response(403), requests.exceptions.SSLError("private-secret")):
            with self.subTest(result=type(result).__name__), patch("pumbility_store._connect") as connect:
                with patch.object(store._binary_transport, "request", side_effect=result if isinstance(result, Exception) else None,
                                  return_value=result):
                    with self.assertRaises(requests.exceptions.RequestException):
                        store.put_bytes("private-key", b"payload", content_type="application/octet-stream")
                connect.assert_not_called()

    def test_download_still_checks_bytes_against_metadata_and_reports_missing_objects(self) -> None:
        store = self.store()
        cursor = MagicMock()
        cursor.fetchone.return_value = (EXPECTED_PUMBILITY_MIGRATION, True, hashlib.sha256(b"payload").hexdigest(), 7, "private-key")
        with patch("pumbility_store._read_cursor") as read, patch.object(store._binary_transport, "request") as request:
            read.return_value.__enter__.return_value = cursor
            request.return_value = response()
            self.assertEqual(store.get_bytes("private-key"), b"payload")
            request.return_value = response(body=b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                store.get_bytes("private-key")
            request.return_value = response(404)
            with self.assertRaisesRegex(ValueError, "missing from Storage"):
                store.get_bytes("private-key")
