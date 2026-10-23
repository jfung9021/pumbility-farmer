import { NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../lib/local-analysis";
import { readLocalOfficialComparisonPlayer, OfficialComparisonNotFoundError, OfficialComparisonGenerationError } from "../../../lib/local-official-comparison";
import { isOfficialPlayerKey, OfficialComparisonValidationError } from "../../../lib/official-comparison";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  if (!localAnalysisEnabled()) return NextResponse.json({ error: "Local analysis mode is disabled." }, { status: 404, headers });
  const params = new URL(request.url).searchParams;
  const bpmPolicy = params.get("bpmPolicy") || undefined;
  if (bpmPolicy && bpmPolicy !== "focal-v1") return NextResponse.json({error:"Invalid BPM policy."}, {status:400, headers});
  const playerKey = params.get("playerKey") || "";
  if (!isOfficialPlayerKey(playerKey)) return NextResponse.json({ error: "A valid official playerKey is required." }, { status: 400, headers });
  try {
    return NextResponse.json(await readLocalOfficialComparisonPlayer(playerKey, params.get("generation") || undefined, bpmPolicy), { headers });
  } catch (error) {
    const status = error instanceof OfficialComparisonGenerationError ? 409
      : error instanceof OfficialComparisonNotFoundError ? 404
      : error instanceof OfficialComparisonValidationError ? 503 : 500;
    return NextResponse.json({ error: status === 500 ? "The selected official player could not be read." : (error as Error).message }, { status, headers });
  }
}
