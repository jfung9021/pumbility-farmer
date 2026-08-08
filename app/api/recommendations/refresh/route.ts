import { NextResponse } from "next/server";

import { localAnalysisEnabled } from "../../../../lib/local-analysis";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local recommendation mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  return NextResponse.json(
    { error: "Live player refresh is unavailable in standalone local mode." },
    { status: 404, headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST() {
  if (!localAnalysisEnabled()) {
    return NextResponse.json(
      { error: "Local recommendation mode is disabled." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  return NextResponse.json(
    { error: "Live player refresh is unavailable in standalone local mode." },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
