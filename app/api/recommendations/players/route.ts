import { NextResponse } from "next/server";

import { localAnalysisEnabled } from "../../../../lib/local-analysis";
import {
  LocalRecommendationsNotFoundError,
  LocalRecommendationsValidationError,
  readLocalRecommendationIndex,
  recommendationPlayerList,
} from "../../../../lib/local-recommendations";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const PLAYER_LIST_CACHE_CONTROL = "public, max-age=300, s-maxage=300, stale-while-revalidate=3600";

export async function GET() {
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local recommendation mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const payload = await readLocalRecommendationIndex();
    return NextResponse.json(recommendationPlayerList(payload), {
      headers: { "Cache-Control": PLAYER_LIST_CACHE_CONTROL },
    });
  } catch (error) {
    if (error instanceof LocalRecommendationsNotFoundError) {
      return NextResponse.json(
        { error: "No local recommendations yet. Run npm run analyze:recommendations." },
        { status: 404, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (error instanceof LocalRecommendationsValidationError) {
      return NextResponse.json(
        { error: error.message },
        { status: 422, headers: { "Cache-Control": "no-store" } },
      );
    }
    return NextResponse.json(
      { error: "The local recommendation player list could not be read." },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}
