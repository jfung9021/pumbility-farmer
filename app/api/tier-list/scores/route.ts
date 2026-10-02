import { NextRequest, NextResponse } from "next/server";

import {
  LocalAnalysisNotFoundError, LocalAnalysisValidationError,
  localAnalysisEnabled, readLocalCombinedAnalysisPayload,
} from "../../../../lib/local-analysis";
import {
  LocalRecommendationsNotFoundError, LocalRecommendationsValidationError,
  readLocalRecommendationIndex,
} from "../../../../lib/local-recommendations";
import {
  LocalTierScoresNotFoundError, LocalTierScoresValidationError,
  localTierScoresForPlayer, readLocalTierScores,
} from "../../../../lib/local-tier-scores";
import type { ModeKey } from "../../../../lib/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = { "Cache-Control": "no-store" };

export async function GET(request: NextRequest) {
  if (!localAnalysisEnabled()) {
    return NextResponse.json({ error: "Local analysis mode is disabled." }, { status: 404, headers });
  }
  const playerKey = request.nextUrl.searchParams.get("playerKey")?.trim() || "";
  const modeValue = request.nextUrl.searchParams.get("mode")?.trim().toLowerCase() || "";
  if (!playerKey || !["singles", "doubles", "coop"].includes(modeValue)) {
    return NextResponse.json(
      { error: "A playerKey and mode of singles, doubles, or coop are required." },
      { status: 400, headers },
    );
  }
  const mode = modeValue as ModeKey;
  try {
    const index = await readLocalRecommendationIndex();
    if (!index.players.some((player) => player.playerKey === playerKey)) {
      return NextResponse.json({ error: "The selected player was not found." }, { status: 404, headers });
    }
    const [tiers, artifact] = await Promise.all([
      readLocalCombinedAnalysisPayload(), readLocalTierScores(),
    ]);
    const payload = localTierScoresForPlayer(
      artifact, playerKey, mode, new Set((tiers[mode] ?? []).map((chart) => chart.chartId)),
    );
    if (!payload) {
      return NextResponse.json({ error: "The selected player's scores are unavailable." }, { status: 404, headers });
    }
    return NextResponse.json(payload, { headers });
  } catch (error) {
    if (error instanceof LocalTierScoresNotFoundError || error instanceof LocalAnalysisNotFoundError
      || error instanceof LocalRecommendationsNotFoundError) {
      return NextResponse.json({ error: error.message }, { status: 404, headers });
    }
    if (error instanceof LocalTierScoresValidationError || error instanceof LocalAnalysisValidationError
      || error instanceof LocalRecommendationsValidationError) {
      return NextResponse.json({ error: error.message }, { status: 422, headers });
    }
    return NextResponse.json({ error: "The selected player's scores could not be read." }, { status: 500, headers });
  }
}
