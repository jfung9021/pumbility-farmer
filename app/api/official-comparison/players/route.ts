import { NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../../lib/local-analysis";
import { readLocalOfficialComparisonIndex, OfficialComparisonNotFoundError } from "../../../../lib/local-official-comparison";
import { OfficialComparisonValidationError } from "../../../../lib/official-comparison";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const headers = { "Cache-Control": "no-store" };
  if (!localAnalysisEnabled()) return NextResponse.json({ error: "Local analysis mode is disabled." }, { status: 404, headers });
  try {
    return NextResponse.json(await readLocalOfficialComparisonIndex(), { headers });
  } catch (error) {
    const known = error instanceof OfficialComparisonNotFoundError || error instanceof OfficialComparisonValidationError;
    return NextResponse.json({ error: known ? error.message : "The official comparison catalog could not be read." }, { status: known ? 503 : 500, headers });
  }
}
