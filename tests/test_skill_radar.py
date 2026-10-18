from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.skill_radar import router
from pumbility_contract import (combined_tier_blob_path, recommendation_blob_path,
    recommendation_phoenix2_shard_path, recommendation_player_state_path)
from skill_radar import (aggregate_bpm_progression, bpm_bucket, build_skill_radar,
    build_suggestion_catalog, calculate_phoenix2_pumbility, pg_equivalent)
from skill_radar_runtime import (SKILL_METADATA_PATH, SkillRadarIneligibleError,
    read_skill_radar, read_skill_radar_players, skill_metadata, _cache, _catalog_cache)

KEY = "a"*20
STAMP = "2026-10-18T00:00:00+00:00"


def chart(cid, mode="Single", level=24, bpm=155, **overrides):
    return {"id": cid, "type": mode, "level": level, "songName": "Song "+cid,
        "bpmMin": bpm, "bpmMax": bpm, **overrides}


def score(cid, **overrides):
    return {"playerId": "private-player", "chartId": cid, "score": 1_000_000,
        "plate": "PG", "recordedAt": STAMP, "isBroken": False, **overrides}


def tiers_for(charts, ratings=None):
    result = {"generatedAtUtc": STAMP, "singles": [], "doubles": []}
    for index, row in enumerate(charts):
        mode = "singles" if row["type"] == "Single" else "doubles"
        result[mode].append({**row, "chartId": row["id"], "difficulty": ("S" if mode == "singles" else "D")+str(row["level"]),
            "pumbilityVariants": {"sssplus": {"estimatedDifficulty": ratings[index] if ratings else row["level"]+.5}}})
    return result


def profile(charts, scores, tiers=None):
    return build_skill_radar(charts, scores, build_suggestion_catalog(tiers or tiers_for(charts), charts),
        player_key=KEY, display_name="PLAYER", generated_at_utc=STAMP)


class TrackerParityTests(unittest.TestCase):
    # Exact fixtures from tracker237e9f0 tests/domain/bpm-progression.test.ts.
    def test_tracker_pricing_fixtures(self):
        cases = [("Single",18,940000,"Fair Game",313.2), ("Double",18,940000,"FG",306.24),
            ("Single",24,995000,"PG",395.2), ("Double",24,920000,"FG",343.0),
            ("Single",15,995000,"UG",318.57), ("Double",18,995000,"UG",333.52),
            ("Single",15,995000,"MG",316.26), ("Single",16,995000,"EG",325.51),
            ("Double",17,995000,"EG",325.08), ("Double",18,995000,"PG",334.4)]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(calculate_phoenix2_pumbility(*args[:-1]), args[-1])
        self.assertEqual(calculate_phoenix2_pumbility("Single",24,994999,"PG"),392.6)
        self.assertEqual(calculate_phoenix2_pumbility("Single",24,995000," perfect game "),395.2)
        for value in (None, float("nan"), -1, True):
            self.assertIsNone(calculate_phoenix2_pumbility("Single",24,value,"PG"))
        self.assertIsNone(calculate_phoenix2_pumbility("Single",24,995000,None))

    def test_tracker_reference_breakpoint_and_sparse_zero_slots(self):
        for mode,total,expected in (("Single",1976,24),("Single",1900,23),("Double",1900,24),
            ("Double",1672,18),("Single",2052,25),("Single",2128,26),("Single",0,0)):
            self.assertAlmostEqual(pg_equivalent(mode,total),expected)
        charts=[chart(str(i)) for i in range(5)]
        result=aggregate_bpm_progression(charts,[score(str(i)) for i in range(4)])
        self.assertEqual(result["buckets"][1]["singles"]["totalPumbility"],1580.8)
        self.assertAlmostEqual(result["buckets"][1]["singles"]["equivalentLevel"],14.6)
        three=aggregate_bpm_progression(charts,[score(str(i)) for i in range(3)])
        self.assertEqual(three["buckets"][1]["singles"]["scoreCount"],3)
        self.assertAlmostEqual(three["buckets"][1]["singles"]["equivalentLevel"],4.2)

    def test_full_interval_boundaries_and_distinct_exclusions(self):
        intervals=[(150,159.999),(150,160),(140,180),(None,None),(150,None),(0,0),(160,150),(math.nan,160)]
        charts=[chart(str(i),bpmMin=low,bpmMax=high) for i,(low,high) in enumerate(intervals)]
        result=aggregate_bpm_progression(charts,[score(str(i)) for i in range(8)])
        self.assertEqual(result["buckets"][1]["singles"]["scoreCount"],1)
        self.assertEqual(result["excluded"]["spanningBpm"],2)
        self.assertEqual(result["excluded"]["unknownBpm"],5)
        self.assertEqual(bpm_bucket(chart("a",bpm=149.999)),0)
        self.assertEqual(bpm_bucket(chart("a",bpm=220)),8)
        result=aggregate_bpm_progression([chart("a")],[score("a",isBroken=True),score("a",isBroken=True),score("unknown"),score("unknown")])
        self.assertEqual(result["excluded"]["broken"],1)
        self.assertEqual(result["excluded"]["unknownChart"],1)

    def test_supplied_values_fallbacks_and_integer_cent_rounding(self):
        charts=[chart(str(i),scoringLevel=18) for i in range(5)]
        rows=[score("0",pumbility=400,score=None,plate=None),score("1",pumbility=math.nan),
            score("2",pumbility=-1),score("3",pumbility=0,plate=None),score("4",pumbility=None,plate=None)]
        result=aggregate_bpm_progression(charts,rows)
        self.assertEqual(result["buckets"][1]["singles"]["totalPumbility"],1190.4)
        self.assertEqual(result["buckets"][1]["singles"]["scoreCount"],4)
        self.assertEqual(result["excluded"]["unpriced"],1)
        cents=aggregate_bpm_progression([chart("x")],[score("x",pumbility=1.125)])
        self.assertEqual(cents["buckets"][1]["singles"]["totalPumbility"],1.13)

    def test_unique_top_five_and_exact_tie_order_do_not_mutate_inputs(self):
        charts=[chart(str(i)) for i in range(7)]
        rows=[score(str(i),pumbility=300+i*10) for i in range(7)]
        rows += [score("0",pumbility=500),score("0",pumbility=1000,isBroken=True)]
        before=copy.deepcopy(rows)
        bar=aggregate_bpm_progression(charts,rows)["buckets"][1]["singles"]
        self.assertEqual(bar["totalPumbility"],1880)
        self.assertEqual(bar["scoreCount"],5)
        self.assertEqual(rows,before)
        tied=[score("0",pumbility=301,score=980000,recordedAt="2026-10-01"),
              score("0",pumbility=301,score=990000,recordedAt="2026-09-01"),
              score("0",pumbility=301,score=990000,recordedAt="2026-10-02")]
        selected=aggregate_bpm_progression(charts,tied)["buckets"][1]["singles"]["contributors"][0]
        self.assertEqual(selected["recordedAt"],"2026-10-02")


class RadarPolicyTests(unittest.TestCase):
    def test_contributions_are_the_exact_unique_top_five_records_used_for_pg(self):
        charts=[chart(str(i),level=12) for i in range(7)]
        rows=[score(str(i),pumbility=300+i) for i in range(7)]
        rows += [score("0",pumbility=500,score=980000,plate="TG"),
                 score("0",pumbility=400,score=999999,plate="PG"),
                 score("1",pumbility=1000,isBroken=True)]
        bucket=profile(charts,rows)["buckets"][2]
        selected=bucket["contributingCharts"]
        self.assertEqual([c["chartId"] for c in selected],["0","6","5","4","3"])
        self.assertEqual([c["pumbility"] for c in selected],[500,306,305,304,303])
        self.assertEqual((selected[0]["score"],selected[0]["plate"]),(980000,"TG"))
        self.assertTrue(all(c["difficulty"]=="S12" for c in selected))
        self.assertAlmostEqual(pg_equivalent("Single",sum(c["pumbility"] for c in selected)),bucket["rawPg"])
        allowed={"chartId","songName","type","level","difficulty","imageUrl","pumbility","score","plate"}
        self.assertTrue(all(set(c)==allowed for c in selected))

    def test_contributions_retain_sparse_cent_rounding_and_empty_slots(self):
        bucket=profile([chart("one",level=12)],
            [score("one",pumbility=1.125,score=None,plate=None)])["buckets"][2]
        self.assertEqual(bucket["selectedCharts"],1)
        self.assertEqual(len(bucket["contributingCharts"]),1)
        self.assertEqual(bucket["contributingCharts"][0]["pumbility"],1.13)
        self.assertIsNone(bucket["contributingCharts"][0]["score"])
        self.assertIsNone(bucket["contributingCharts"][0]["plate"])
        self.assertEqual(profile([],[])["buckets"][0]["contributingCharts"],[])

    def test_double_alignment_and_raw_recommendation_threshold(self):
        charts=[chart("s"+str(i),level=18) for i in range(5)]+[chart("d"+str(i),mode="Double",level=19) for i in range(5)]
        response=profile(charts,[score(c["id"]) for c in charts])
        single,double=response["buckets"][2:4]
        self.assertAlmostEqual(single["rawPg"],18)
        self.assertAlmostEqual(double["rawPg"],19)
        self.assertAlmostEqual(single["displayPg"],double["displayPg"])
        self.assertAlmostEqual(single["radarScore"],double["radarScore"])
        self.assertEqual(double["minimumSuggestedDifficulty"],20)

    def test_empty_and_sparse_groups_partition_all_eighteen_once(self):
        empty=profile([],[])
        self.assertEqual(empty["groups"]["weaknesses"],["singles:0","doubles:0","singles:1"])
        self.assertEqual(empty["groups"]["strengths"],[])
        self.assertTrue(all(row["radarScore"]==0 and row["needsPlays"] for row in empty["buckets"]))
        charts=[chart(str(i)) for i in range(3)]
        sparse=profile(charts,[score(str(i)) for i in range(3)])
        self.assertEqual(sparse["buckets"][2]["selectedCharts"],3)
        self.assertEqual(sparse["groups"]["strengths"],["singles:1"])
        members=sum(sparse["groups"].values(),[])
        self.assertEqual(len(members),18)
        self.assertEqual(len(set(members)),18)

    def test_suggestions_are_five_nearest_rated_charts_include_played_and_respect_bpm(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("c"+str(i),level=20) for i in range(8)]
        candidates += [chart("cross",level=20,bpmMin=150,bpmMax=160)]
        charts=earned+candidates
        ratings=[18.5]*5+[18.99999,19.0,19.1,19.2,19.3,19.4,20.0,None]+[19.0]
        tiers=tiers_for(charts,ratings)
        catalog=build_suggestion_catalog(tiers,charts)
        response=build_skill_radar(charts,[score(c["id"]) for c in earned],catalog,player_key=KEY,
            display_name="PLAYER",generated_at_utc=STAMP,
            personal_scores=[{"chartId":"c1","score":999999,"plateCode":"PG"}])
        suggestions=response["buckets"][2]["suggestions"]
        self.assertEqual([row["chartId"] for row in suggestions],["c1","c2","c3","c4","c5"])
        self.assertEqual(suggestions[0]["personalScore"],999999)
        self.assertEqual(suggestions[0]["personalPlate"],"PG")
        self.assertNotIn("cross",[row["chartId"] for row in catalog["charts"]])
        self.assertEqual(response["buckets"][0]["suggestions"],[])

    def test_recommendations_truncate_after_adding_and_sort_by_calculated_rating(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("c"+str(i),level=22) for i in range(7)]
        rows=[score(c["id"],pumbility=366.32) for c in earned]
        bucket=profile(earned+candidates,rows,
            tiers_for(earned+candidates,[18.5]*5+[22.9,21.99999,22.0,22.2,22.1,22.5,23.0]))["buckets"][2]
        self.assertAlmostEqual(bucket["rawPg"],21.2)
        self.assertEqual(bucket["minimumSuggestedDifficulty"],22)
        self.assertEqual([c["chartId"] for c in bucket["suggestions"]],["c2","c4","c3","c5","c0"])
        for points, expected in ((361,22),(360.99,21)):
            boundary=profile(earned,[score(c["id"],pumbility=points) for c in earned])["buckets"][2]
            self.assertEqual(boundary["minimumSuggestedDifficulty"],expected)

    def test_all_level_skill_evidence_is_not_limited_to_suggestion_catalog(self):
        low=[chart("low"+str(i),level=12) for i in range(5)]
        rated=[chart("rated",level=16)]
        response=profile(low+rated,[score(c["id"]) for c in low],tiers_for(rated))
        bucket=response["buckets"][2]
        self.assertAlmostEqual(bucket["rawPg"],12)
        self.assertEqual(bucket["selectedCharts"],5)
        self.assertEqual([row["chartId"] for row in bucket["suggestions"]],["rated"])


class Store:
    def __init__(self):
        self.charts=[chart(str(i),level=12) for i in range(5)]+[chart("rated",level=16)]
        self.values={recommendation_blob_path():{"generationKey":"daily","players":[{
            "playerKey":KEY,"internalPlayerId":"private-player","inputShard":0,"displayName":"PLAYER","username":"PLAYER"}]},
            recommendation_phoenix2_shard_path("daily",0):{"players":[{"playerId":"private-player",
                "lastSyncedAtUtc":STAMP,"scores":[score(str(i),pumbility=296.4) for i in range(5)]}]},
            combined_tier_blob_path():tiers_for([self.charts[-1]]),SKILL_METADATA_PATH:skill_metadata(self.charts)}
        self.counts={"private-player":100}
    def get_json(self,path):return self.values.get(path)
    def read_skill_radar_score_counts(self,player_id=None):
        return dict(self.counts) if player_id is None else {player_id:self.counts.get(player_id,0)}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        _catalog_cache.clear()

    def test_only_skill_radar_uses_the_100_phoenix2_record_minimum(self):
        store=Store()
        store.values[recommendation_blob_path()]["players"].append({
            "playerKey":"b"*20,"internalPlayerId":"under-minimum","username":"LOW","displayName":"LOW",
            "scoreProgress":{"singles":{"validScoreCount":500,"requiredScoreCount":30}}})
        store.counts["under-minimum"]=99
        self.assertEqual([p["playerKey"] for p in read_skill_radar_players(store)["players"]],[KEY])
        self.assertEqual(len(store.values[recommendation_blob_path()]["players"]),2)
        read_skill_radar(store,KEY)
        store.counts["private-player"]=99
        with self.assertRaisesRegex(SkillRadarIneligibleError,"100 submitted Phoenix 2"):
            read_skill_radar(store,KEY)
        self.assertEqual(read_skill_radar_players(store)["players"],[])

    def test_record_count_query_is_phoenix2_only_across_modes_levels_and_broken_plays(self):
        from pumbility_store import PumbilityArtifactStore
        with patch("pumbility_store._read_cursor") as read, patch("pumbility_store._assert_schema"):
            cursor=read.return_value.__enter__.return_value
            cursor.fetchall.return_value=[("private-player",100)]
            store=PumbilityArtifactStore(database_url="postgresql://local/test")
            self.assertEqual(store.read_skill_radar_score_counts("private-player"),{"private-player":100})
            query,params=cursor.execute.call_args.args
            self.assertEqual(params,("phoenix2","private-player"))
            self.assertIn("sr.valid_to is null",query)
            self.assertIn("count(distinct chart_id)",query)
            self.assertIn("recommendation-player-state/",query)
            self.assertIn("jsonb_array_elements",query)
            self.assertIn("union all",query)
            self.assertNotIn("is_broken",query)

    def test_generation_bound_catalog_cache_avoids_reloading_large_tier_payload(self):
        store=Store()
        store.artifact_revision=lambda path: {"sha256":store.version,"revision":store.version}
        store.version="one"
        with patch.object(store,"get_json",wraps=store.get_json) as reads:
            read_skill_radar(store,KEY)
            reads.reset_mock()
            read_skill_radar(store,KEY)
            self.assertFalse(any(call.args[0]==combined_tier_blob_path() for call in reads.call_args_list))
            store.version="two"
            reads.reset_mock()
            read_skill_radar(store,KEY)
            self.assertTrue(any(call.args[0]==combined_tier_blob_path() for call in reads.call_args_list))

    def test_hosted_all_level_daily_and_live_evidence_are_public_and_cache_invalidates(self):
        store=Store()
        first=read_skill_radar(store,KEY)
        self.assertAlmostEqual(first["buckets"][2]["rawPg"],12)
        self.assertIs(first,read_skill_radar(store,KEY))
        store.values[recommendation_player_state_path(KEY)]={"playerId":"private-player",
            "lastSyncedAtUtc":"2026-10-19T00:00:00Z","scores":[score("0",pumbility=400)]}
        second=read_skill_radar(store,KEY)
        self.assertIsNot(first,second)
        self.assertGreater(second["buckets"][2]["rawPg"],first["buckets"][2]["rawPg"])
        encoded=json.dumps(second)
        self.assertNotIn("private-player",encoded)
        for private in ("recordedAt","contributors","playerId","inputShard","letterGrade"):
            self.assertNotIn('"'+private+'"',encoded)
        self.assertEqual(second["buckets"][2]["contributingCharts"][0]["pumbility"],400)
        store.values[combined_tier_blob_path()]["generatedAtUtc"]="2026-10-20T00:00:00Z"
        third=read_skill_radar(store,KEY)
        self.assertIsNot(second,third)
        # Revocation is checked before consulting the per-profile cache.
        store.values[recommendation_blob_path()]["players"]=[]
        with self.assertRaises(ValueError):read_skill_radar(store,KEY)

    def test_query_errors_and_missing_analysis_do_not_become_zero_profiles(self):
        app=FastAPI();app.include_router(router);client=TestClient(app)
        store=Store()
        with patch("api.skill_radar.PrivateBlobStore",return_value=store):
            for suffix,status in (("",400),("?playerKey=invalid",400),("?playerKey="+"b"*20,404),("?playerKey="+KEY,200)):
                response=client.get("/api/skill-radar"+suffix)
                self.assertEqual(response.status_code,status)
                self.assertEqual(response.headers["cache-control"],"no-store")
            store.values.pop(SKILL_METADATA_PATH)
            unavailable=client.get("/api/skill-radar?playerKey="+KEY)
            self.assertEqual(unavailable.status_code,503)
            self.assertNotIn("buckets",unavailable.json())
            store.counts["private-player"]=99
            rejected=client.get("/api/skill-radar?playerKey="+KEY)
            self.assertEqual(rejected.status_code,403)
            self.assertIn("100 submitted Phoenix 2",rejected.json()["error"])
            listed=client.get("/api/skill-radar/players")
            self.assertEqual(listed.status_code,200)
            self.assertEqual(listed.json()["players"],[])

    def test_local_artifact_uses_same_pure_profile_and_atomic_player_file(self):
        from scripts.build_local_skill_radar import build
        from piu_recommendations import public_player_key
        charts=[chart(str(i),level=12) for i in range(5)]+[chart("rated",level=16)]
        rows=[score(str(i),pumbility=296.4) for i in range(5)]
        rows += [score("broken"+str(i),isBroken=True) for i in range(95)]
        rows += [score("low"+str(i),playerId="below-minimum",isBroken=True) for i in range(99)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tiers=root/"tiers.json";tiers.write_text(json.dumps(tiers_for([charts[-1]])),encoding="utf-8")
            with patch("scripts.build_local_skill_radar.load_snapshot",return_value=([
                {"playerId":"private-player","username":"PLAYER","lastSyncedAtUtc":STAMP},
                {"playerId":"below-minimum","username":"LOW","lastSyncedAtUtc":STAMP}],charts,rows)):
                report=build(root,root/"output",tiers_path=tiers)
            key=public_player_key("private-player")
            saved=json.loads((root/"output/players"/(key+".json")).read_text(encoding="utf-8"))
            self.assertEqual(report["scoreFetches"],0)
            self.assertAlmostEqual(saved["buckets"][2]["rawPg"],12)
            roster=json.loads((root/"output/players.json").read_text(encoding="utf-8"))
            self.assertEqual([p["playerKey"] for p in roster["players"]],[key])
            self.assertEqual(roster["players"][0]["phoenix2ScoreCount"],100)
            self.assertFalse(list((root/"output").rglob("*.tmp")))
            hosted=Store()
            hosted.values[recommendation_blob_path()]["players"][0]["playerKey"]=key
            live_profile=read_skill_radar(hosted,key)
            self.assertEqual({k:v for k,v in saved.items() if k!="generatedAtUtc"},
                             {k:v for k,v in live_profile.items() if k!="generatedAtUtc"})


if __name__ == "__main__":
    unittest.main()
