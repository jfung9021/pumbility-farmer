import assert from "node:assert/strict";
import test from "node:test";

import { readJsonResponse } from "../lib/api-response.ts";


test("uses a JSON error when the backend supplies one", async () => {
  const response = new Response(JSON.stringify({ error: "Safe backend error" }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });
  await assert.rejects(() => readJsonResponse(response), /Safe backend error/);
});

test("turns platform-generated text into a useful error", async () => {
  const response = new Response("FUNCTION_INVOCATION_TIMEOUT", { status: 504 });
  await assert.rejects(
    () => readJsonResponse(response),
    (error: unknown) => error instanceof Error
      && error.message === "FUNCTION_INVOCATION_TIMEOUT"
      && !error.message.includes("Unexpected token"),
  );
});

test("rejects an empty successful response as non-JSON", async () => {
  const response = new Response("", { status: 200 });
  await assert.rejects(() => readJsonResponse(response), /empty or non-JSON/);
});
