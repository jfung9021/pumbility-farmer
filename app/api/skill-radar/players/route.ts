import { NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../../lib/local-analysis";
import { readLocalSkillRadarPlayers } from "../../../../lib/local-skill-radar";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = {"Cache-Control":"no-store"};

export async function GET(request: Request) {
  if (!localAnalysisEnabled()) return NextResponse.json({error:"Local analysis mode is disabled."}, {status:404, headers});
  const bpmPolicy = new URL(request.url).searchParams.get("bpmPolicy") || undefined;
  if (bpmPolicy && bpmPolicy !== "focal-v1") return NextResponse.json({error:"Invalid BPM policy."}, {status:400, headers});
  try {
    return NextResponse.json(await readLocalSkillRadarPlayers(undefined, bpmPolicy), {headers});
  } catch {
    return NextResponse.json({error:"The Skill Radar player list is temporarily unavailable."}, {status:503, headers});
  }
}
