import { NextResponse } from "next/server";

import {
  LocalAnalysisNotFoundError,
  LocalAnalysisValidationError,
  localAnalysisEnabled,
  readLocalCombinedAnalysisPayload,
} from "../../../lib/local-analysis";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local analysis mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const payload = await readLocalCombinedAnalysisPayload();
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (error) {
    if (error instanceof LocalAnalysisNotFoundError) {
      return NextResponse.json(
        { error: "No local combined tier list exists. Run npm run analyze:recommendations." },
        { status: 404, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (error instanceof LocalAnalysisValidationError) {
      return NextResponse.json(
        { error: error.message },
        { status: 422, headers: { "Cache-Control": "no-store" } },
      );
    }
    return NextResponse.json(
      { error: "The local combined tier list could not be read." },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}
