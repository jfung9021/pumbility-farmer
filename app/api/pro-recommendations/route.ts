import { NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../lib/local-analysis";
import { isProPlayerKey, ProRecommendationValidationError } from "../../../lib/pro-recommendations";
import { readLocalProRecommendations, ProRecommendationsGenerationError, ProRecommendationsNotFoundError, ProRecommendationsUnavailableError } from "../../../lib/local-pro-recommendations";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  if (!localAnalysisEnabled()) return NextResponse.json({ error: "Local analysis mode is disabled." }, { status: 404, headers });
  const params = new URL(request.url).searchParams;
  if ((params.has("mode") && !["overall", "singles", "doubles"].includes(params.get("mode")!))
    || (params.has("scope") && params.get("scope") !== "mode-consolidated")) {
    return NextResponse.json({ error: "Choose Overall, Singles or Doubles for Pro Farming." }, { status: 400, headers });
  }
  const playerKey = params.get("playerKey") || "";
  if (!isProPlayerKey(playerKey)) return NextResponse.json({ error: "A valid playerKey is required." }, { status: 400, headers });
  try { return NextResponse.json(await readLocalProRecommendations(playerKey, params.get("generation") || undefined), { headers }); }
  catch (error) {
    const status = error instanceof ProRecommendationsGenerationError ? 409 : error instanceof ProRecommendationsNotFoundError ? 404
      : error instanceof ProRecommendationsUnavailableError || error instanceof ProRecommendationValidationError ? 503 : 500;
    return NextResponse.json({ error: status === 500 ? "The Pro Farming profile could not be read." : (error as Error).message }, { status, headers });
  }
}
