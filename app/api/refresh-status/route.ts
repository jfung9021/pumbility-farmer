import { NextResponse } from "next/server";

/** Hosted requests are routed to Python; standalone local mode has no scheduler. */
export function GET() {
  return NextResponse.json({ error: "Refresh status is temporarily unavailable." }, { status: 503, headers: { "Cache-Control": "no-store" } });
}
