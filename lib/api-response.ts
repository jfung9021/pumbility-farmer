export async function readJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let body: unknown = null;
  if (text.trim()) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const jsonError = body && typeof body === "object" && "error" in body
      ? String((body as { error: unknown }).error)
      : "";
    const fallback = text.trim().replace(/\s+/g, " ").slice(0, 180);
    throw new Error(jsonError || fallback || `Request failed with HTTP ${response.status}.`);
  }
  if (body === null) throw new Error("The server returned an empty or non-JSON response.");
  return body as T;
}
