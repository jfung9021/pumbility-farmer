import { NextRequest, NextResponse } from "next/server";

import {
  LocalAnalysisNotFoundError,
  LocalAnalysisValidationError,
  localAnalysisEnabled,
  readLocalAnalysisPayload,
} from "../../../lib/local-analysis";
import { DEFAULT_MIX, isMixKey, MIXES } from "../../../lib/mixes";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const requestedMix = request.nextUrl.searchParams.get("mix") || DEFAULT_MIX;
  if (!isMixKey(requestedMix)) {
    return NextResponse.json(
      { error: "Unsupported mix. Expected phoenix1 or phoenix2." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const archive = MIXES[requestedMix].archive;
  if (archive) {
    return NextResponse.redirect(new URL(archive.url, request.url), 307);
  }
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local analysis mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const payload = await readLocalAnalysisPayload(requestedMix);
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (error) {
    if (error instanceof LocalAnalysisNotFoundError) {
      return NextResponse.json(
        { error: `No local analysis has been generated yet. Run npm run analyze:${requestedMix}.` },
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
      { error: "The local analysis could not be read." },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}
