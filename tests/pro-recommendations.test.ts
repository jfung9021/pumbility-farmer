import assert from "node:assert/strict";
import {mkdtemp,mkdir,rm,writeFile} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {PRO_RECOMMENDATION_METHOD,validateProPlayersResponse,validateProRecommendationsResponse,
  type ProPlayersResponse,type ProRecommendationsResponse,type ProRecommendationChart} from "../lib/pro-recommendations.ts";
import {readLocalProPlayers,readLocalProRecommendations,ProRecommendationsGenerationError,ProRecommendationsNotFoundError} from "../lib/local-pro-recommendations.ts";

function fixture(): {index:ProPlayersResponse;profile:ProRecommendationsResponse} {
  const metadata={schemaVersion:2,method:PRO_RECOMMENDATION_METHOD,mix:"Phoenix2",scope:"mode-consolidated",
    generation:"a".repeat(64),snapshotAt:"2026-11-22T17:33:26Z",fetchedAtUtc:"2026-11-23T03:00:00Z",generatedAtUtc:"2026-11-23T03:05:00Z"} as const;
  const player={playerKey:"b".repeat(20),displayName:"Example",officialDisplayName:"EXAMPLE#1234",overallRank:100,overallPumbility:19000};
  const row:ProRecommendationChart={chartId:"chart-a",songName:"Example",chartLabel:"S24",type:"Single",level:24,sourceMode:"singles",
    rawPercentileScore:996000,gradeBoundaryScore:995000,targetScore:995000,targetGrade:"SSS+",assumedPlate:"SG",expectedPumbility:392.08,
    projectedGain:34.88,currentScore:null,currentPumbility:null,currentPlate:null,currentPlateEstimated:false,
    peerScoreCount:12,cohortPlayerCount:15,boardPossiblyTruncated:false,limitedData:false};
  const states={singles:{status:"ready" as const,modePumbility:17860},doubles:{status:"insufficient-data" as const,modePumbility:null}};
  return {index:{...metadata,players:[{...player,status:"ready",eligible:true,modes:states}]},profile:{...metadata,player,status:"ready",message:null,eligible:true,
    modes:{singles:{mode:"singles",generation:metadata.generation,status:"ready",message:null,modePumbility:17860,
      baseline:{mode:"singles",source:"piuscores",estimated:false,scoreCount:100,top50Pumbility:17860,cutoffPumbility:357.2,
        syncedAtUtc:metadata.fetchedAtUtc,snapshotAt:metadata.snapshotAt,fallbackReason:null,
        top50:Array.from({length:50},(_,i)=>({chartId:`base-${i}`,songName:`Song ${i}`,chartLabel:"S20",type:"Single" as const,level:20,
          score:1000000,grade:"SSS+",plate:"PG",plateEstimated:false,pumbility:357.2})).sort((a,b)=>a.chartId.localeCompare(b.chartId))},
      cohort:{minimumPlayers:10,upwardWindow:100,backfillCount:0,playerCount:15,minimumPumbility:17860,maximumPumbility:17900,limitedData:false},
      recommendations:[row],candidateCount:47,unobservedChartCount:0},
      doubles:{mode:"doubles",generation:metadata.generation,status:"insufficient-data",message:"Fewer than 50 charts",modePumbility:null,
        baseline:null,cohort:null,recommendations:[],candidateCount:128,unobservedChartCount:0}},
    overall:{kind:"overall-top50",generation:metadata.generation,sourceGenerations:{singles:metadata.generation,doubles:metadata.generation},
      baseline:{mode:"overall",source:"piuscores",estimated:false,scoreCount:100,top50Pumbility:17860,cutoffPumbility:357.2,
        syncedAtUtc:metadata.fetchedAtUtc,snapshotAt:metadata.snapshotAt,fallbackReason:null,
        top50:Array.from({length:50},(_,i)=>({chartId:`base-${i}`,songName:`Song ${i}`,chartLabel:"S20",type:"Single" as const,level:20,
          score:1000000,grade:"SSS+",plate:"PG",plateEstimated:false,pumbility:357.2}))
          .sort((a,b)=>a.chartId<b.chartId?-1:a.chartId>b.chartId?1:0)},
      availableModes:["singles"],unavailableModes:["doubles"],recommendations:[{...row,modeProjectedGain:row.projectedGain,replacedPumbility:357.2}]}}};
}
function withRow(profile:ProRecommendationsResponse,row:ProRecommendationChart):ProRecommendationsResponse {
  return {...profile,modes:{...profile.modes,singles:{...profile.modes.singles,recommendations:[row]}},overall:{...profile.overall,recommendations:[{...row,modeProjectedGain:row.projectedGain,replacedPumbility:357.2}]}};
}
test("Pro contracts accept independent modes and reject legacy or mixed calculation scopes",()=>{
  const {index,profile}=fixture();
  assert.equal(validateProPlayersResponse(index),index);
  assert.equal(validateProRecommendationsResponse(profile,index),profile);
  assert.throws(()=>validateProRecommendationsResponse({...profile,schemaVersion:1}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,method:"pro-pumbility-p75-modes-consolidated-phoenix2-v3"}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,scope:"overall"}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,mix:"Phoenix"}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,baseline:{}}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,player:{...profile.player,overallRank:301}}));
  assert.throws(()=>validateProRecommendationsResponse(withRow(profile,{...profile.modes.singles.recommendations[0],level:23})));
});
test("Overall must preserve mode targets and use the reconstructed combined Top 50 for gains",()=>{
  const {profile}=fixture();
  const row=profile.overall.recommendations[0];
  assert.throws(()=>validateProRecommendationsResponse({...profile,overall:{...profile.overall,recommendations:[{...row,projectedGain:row.projectedGain+1}]}}),/preserve/);
  assert.throws(()=>validateProRecommendationsResponse({...profile,overall:{...profile.overall,recommendations:[]}}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,overall:{...profile.overall,recommendations:[{...row,modeProjectedGain:row.modeProjectedGain+1}]}}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,overall:{...profile.overall,baseline:null}}));
  assert.throws(()=>validateProRecommendationsResponse({...profile,modes:{...profile.modes,singles:{...profile.modes.singles,generation:"stale"}}}));
});
test("Pro plate thresholds use raw q50 and preserve exact-million PG",()=>{
  const {profile}=fixture();
  for(const [rawPercentileScore,assumedPlate,targetScore] of [[995000,"MG",995000],[995000.25,"SG",995000],[999999.99,"SG",995000],[1000000,"PG",1000000]] as const){
    const row={...profile.modes.singles.recommendations[0],rawPercentileScore,assumedPlate,targetScore};
    assert.doesNotThrow(()=>validateProRecommendationsResponse(withRow(profile,row)));
    assert.throws(()=>validateProRecommendationsResponse(withRow(profile,{...row,assumedPlate:assumedPlate==="MG"?"SG":"MG"})));
  }
});
test("Pro local readers use one immutable mode generation and preserve all views",async()=>{
  const dir=await mkdtemp(path.join(os.tmpdir(),"pro-modes-"));const previous=process.env.PIU_LOCAL_PRO_RECOMMENDATIONS_DIR;
  process.env.PIU_LOCAL_PRO_RECOMMENDATIONS_DIR=dir;
  try{
    const {index,profile}=fixture();const players=path.join(dir,"generations",index.generation,"players");
    await mkdir(players,{recursive:true});await writeFile(path.join(dir,"index.json"),JSON.stringify(index));
    await writeFile(path.join(players,profile.player.playerKey+".json"),JSON.stringify(profile));
    assert.equal((await readLocalProPlayers()).players.length,1);
    assert.equal((await readLocalProRecommendations(profile.player.playerKey)).overall.kind,"overall-top50");
    await assert.rejects(readLocalProRecommendations(profile.player.playerKey,"stale"),ProRecommendationsGenerationError);
    await assert.rejects(readLocalProRecommendations("c".repeat(20)),ProRecommendationsNotFoundError);
    await assert.rejects(readLocalProRecommendations("../snapshot"),ProRecommendationsNotFoundError);
  }finally{if(previous===undefined)delete process.env.PIU_LOCAL_PRO_RECOMMENDATIONS_DIR;else process.env.PIU_LOCAL_PRO_RECOMMENDATIONS_DIR=previous;await rm(dir,{recursive:true,force:true});}
});
