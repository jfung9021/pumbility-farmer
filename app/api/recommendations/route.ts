import { NextRequest, NextResponse } from "next/server";

import { localAnalysisEnabled } from "../../../lib/local-analysis";
import {
  LocalRecommendationsNotFoundError,
  LocalRecommendationsValidationError,
  readLocalRecommendationIndex,
  readLocalRecommendationPlayer,
  recommendationsForMode,
  recommendationsForPlayer,
  recommendationsForRating,
} from "../../../lib/local-recommendations";
import type { RecommendationModeKey } from "../../../lib/types";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const RECOMMENDATION_MODE_KEYS = new Set<RecommendationModeKey>([
  "overall",
  "singles",
  "doubles",
  "coop",
]);

export async function GET(request: NextRequest) {
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local recommendation mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  const playerKey = request.nextUrl.searchParams.get("playerKey")?.trim() || "";
  const modeValue = request.nextUrl.searchParams.get("mode")?.trim().toLowerCase() || "";
  if (modeValue && !RECOMMENDATION_MODE_KEYS.has(modeValue as RecommendationModeKey)) {
    return NextResponse.json(
      { error: "mode must be one of overall, singles, doubles, or coop." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const mode = modeValue as RecommendationModeKey | "";
  const difficulty = request.nextUrl.searchParams.get("difficulty")?.trim() || "";
  if (difficulty && mode !== "overall") {
    return NextResponse.json(
      { error: "difficulty is only valid with mode=overall." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const ratingValue = request.nextUrl.searchParams.get("rating")?.trim() || "";
  const rating = ratingValue ? Number(ratingValue) : Number.NaN;
  if (!playerKey && (!Number.isFinite(rating) || rating < 1 || rating > 40)) {
    return NextResponse.json(
      { error: "A playerKey or a skill rating from 1 to 40 is required." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const payload = await readLocalRecommendationIndex();
    if (!playerKey) {
      const response = recommendationsForRating(payload, rating);
      return NextResponse.json(mode ? recommendationsForMode(response, mode, difficulty) : response, {
        headers: { "Cache-Control": "no-store, max-age=0" },
      });
    }
    const player = await readLocalRecommendationPlayer(payload, playerKey);
    const response = recommendationsForPlayer(payload, playerKey, player);
    if (!response) {
      return NextResponse.json(
        { error: "The selected recommendation player was not found." },
        { status: 404, headers: { "Cache-Control": "no-store" } },
      );
    }
    return NextResponse.json(mode ? recommendationsForMode(response, mode, difficulty) : response, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (error) {
    if (error instanceof RangeError) {
      return NextResponse.json(
        { error: error.message },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }
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
      { error: "The selected local recommendations could not be read." },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}
