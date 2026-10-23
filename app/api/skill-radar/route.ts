import { NextRequest, NextResponse } from "next/server";
import { localAnalysisEnabled } from "../../../lib/local-analysis";
import { readLocalSkillRadar, SkillRadarEligibilityError } from "../../../lib/local-skill-radar";
import { PLAYER_KEY_PATTERN } from "../../../lib/skill-radar";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = {"Cache-Control":"no-store"};

export async function GET(request: NextRequest) {
  if (!localAnalysisEnabled()) return NextResponse.json({error:"Local analysis mode is disabled."}, {status:404, headers});
  const playerKey = request.nextUrl.searchParams.get("playerKey")?.trim() ?? "";
  const bpmPolicy = request.nextUrl.searchParams.get("bpmPolicy") || undefined;
  if (bpmPolicy && bpmPolicy !== "focal-v1") return NextResponse.json({error:"Invalid BPM policy."}, {status:400, headers});
  if (!PLAYER_KEY_PATTERN.test(playerKey)) return NextResponse.json({error:"A valid playerKey is required."}, {status:400, headers});
  try {
    return NextResponse.json(await readLocalSkillRadar(playerKey, undefined, undefined, bpmPolicy), {headers});
  } catch (error) {
    if (error instanceof SkillRadarEligibilityError) return NextResponse.json({error:error.message}, {status:403, headers});
    const absent = (error as NodeJS.ErrnoException).code === "ENOENT";
    return NextResponse.json({error:absent ? "Skill Radar has not been generated for this player yet." : "Skill Radar is temporarily unavailable."}, {status:absent ? 404 : 503, headers});
  }
}
