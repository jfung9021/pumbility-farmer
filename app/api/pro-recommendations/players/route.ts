import { NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../../lib/local-analysis";
import { ProRecommendationValidationError } from "../../../../lib/pro-recommendations";
import { readLocalProPlayers, ProRecommendationsUnavailableError } from "../../../../lib/local-pro-recommendations";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const headers = { "Cache-Control": "no-store" };
  if (!localAnalysisEnabled()) return NextResponse.json({ error: "Local analysis mode is disabled." }, { status: 404, headers });
  try { return NextResponse.json(await readLocalProPlayers(), { headers }); }
  catch (error) {
    const known = error instanceof ProRecommendationsUnavailableError || error instanceof ProRecommendationValidationError;
    return NextResponse.json({ error: known ? error.message : "The Pro Farming player list could not be read." }, { status: known ? 503 : 500, headers });
  }
}
