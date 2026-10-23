import { NextRequest, NextResponse } from "next/server";
import { localAnalysisEnabled, readLocalCombinedAnalysisPayload, LocalAnalysisNotFoundError, LocalAnalysisValidationError } from "../../../../lib/local-analysis";
import { tierChartDetails } from "../../../../lib/tier-browser-payload";
import { currentBpmMetadata, selectBpmPayload, validateBpmPolicy } from "../../../../lib/server-song-bpm";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = {"Cache-Control":"no-store"};

export async function GET(request: NextRequest) {
  if (!localAnalysisEnabled()) return NextResponse.json({error:"Local analysis mode is disabled."}, {status:404, headers});
  const chartId = request.nextUrl.searchParams.get("chartId")?.trim();
  const generation = request.nextUrl.searchParams.get("generatedAtUtc")?.trim();
  const bpmPolicy = request.nextUrl.searchParams.get("bpmPolicy");
  try { validateBpmPolicy(bpmPolicy); } catch (error) {
    return NextResponse.json({error: (error as Error).message}, {status: 400, headers});
  }
  if (!chartId || !generation) return NextResponse.json({error:"A chartId and generatedAtUtc are required."}, {status:400, headers});
  try {
    const payload = await readLocalCombinedAnalysisPayload();
    if (generation !== payload.generatedAtUtc) return NextResponse.json({error:"The tier list has changed. Reload it to view this chart."}, {status:409, headers});
    if (bpmPolicy && request.nextUrl.searchParams.get("bpmMetadataRevision") !== currentBpmMetadata.bpmMetadataRevision) return NextResponse.json({error:"The BPM metadata has changed. Reload the tier list."}, {status:409, headers});
    const details = tierChartDetails(payload, chartId);
    return details ? NextResponse.json(selectBpmPayload(details, bpmPolicy), {headers}) : NextResponse.json({error:"The selected chart was not found."}, {status:404, headers});
  } catch (error) {
    const status = error instanceof LocalAnalysisNotFoundError ? 404 : error instanceof LocalAnalysisValidationError ? 422 : 500;
    return NextResponse.json({error:"Chart details are temporarily unavailable."}, {status, headers});
  }
}
