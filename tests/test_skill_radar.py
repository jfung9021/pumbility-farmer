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
    build_suggestion_catalog, calculate_phoenix2_pumbility, reference_equivalent_level,
    uncapped_radar_score, BPM_BUCKETS, METHOD_VERSION, PROFILE_VERSION, REFERENCE, MODES)
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

    def test_ug_reference_breakpoint_and_available_chart_average(self):
        for mode,total,expected in (("Single",1972.1,24),("Single",1896.25,23),("Double",1895,24),
            ("Double",1667.6,18),("Single",2047.95,25),("Single",2123.8,26),("Single",0,0)):
            self.assertAlmostEqual(reference_equivalent_level(mode,total),expected)
        self.assertAlmostEqual(reference_equivalent_level("Single",341.32*5),17.999340804218846)
        charts=[chart(str(i)) for i in range(5)]
        for count in range(1,6):
            result=aggregate_bpm_progression(charts,[score(str(i),score=995000,plate="UG") for i in range(count)])
            self.assertAlmostEqual(result["buckets"][1]["singles"]["totalPumbility"],394.42*count)
            self.assertEqual(result["buckets"][1]["singles"]["scoreCount"],count)
            self.assertAlmostEqual(result["buckets"][1]["singles"]["equivalentLevel"],24)
        self.assertEqual(reference_equivalent_level("Single",0,0),0)

    def test_available_pumbility_is_averaged_before_piecewise_inversion(self):
        for mode,levels,expected in (("Single",(21,25),23.5),("Double",(22,26),24.5)):
            charts=[chart(str(i),mode=mode,level=level) for i,level in enumerate(levels)]
            bucket=aggregate_bpm_progression(charts,[score(c["id"],score=995000,plate="UG") for c in charts])["buckets"][1]["singles" if mode=="Single" else "doubles"]
            self.assertAlmostEqual(bucket["equivalentLevel"],expected)
        charts=[chart("zero"),chart("clear",level=25)]
        bucket=aggregate_bpm_progression(charts,[score("zero",pumbility=0),score("clear",score=995000,plate="UG")])["buckets"][1]["singles"]
        self.assertEqual(bucket["scoreCount"],2)
        self.assertAlmostEqual(bucket["equivalentLevel"],0)

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
    def test_contributions_are_the_exact_unique_top_five_records_used_for_equivalent_level(self):
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
        self.assertAlmostEqual(reference_equivalent_level("Single",sum(c["pumbility"] for c in selected),len(selected)),bucket["rawEquivalentLevel"])
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
        charts=[chart("s"+str(i),level=23) for i in range(5)]+[chart("d"+str(i),mode="Double",level=24) for i in range(5)]
        response=profile(charts,[score(c["id"],score=995000,plate="UG") for c in charts])
        single,double=response["buckets"][2:4]
        self.assertAlmostEqual(single["rawEquivalentLevel"],23)
        self.assertAlmostEqual(double["rawEquivalentLevel"],24)
        self.assertAlmostEqual(single["displayEquivalentLevel"],double["displayEquivalentLevel"])
        self.assertGreater(single["radarScore"],double["radarScore"])
        self.assertEqual(double["suggestedLevel"],25)

    def test_ug_reference_keeps_actual_pg_bonus_and_scale_anchors(self):
        self.assertEqual(METHOD_VERSION,6)
        self.assertEqual(PROFILE_VERSION,11)
        for mode,level,expected_rating in (("Single",10,0),("Double",11,0),
                ("Single",25,100),("Double",26,100)):
            charts=[chart(str(i),mode=mode,level=level) for i in range(5)]
            bucket=profile(charts,[score(c["id"],score=995000,plate="UG") for c in charts])["buckets"][2 if mode=="Single" else 3]
            self.assertAlmostEqual(bucket["radarScore"],expected_rating)
        charts=[chart(str(i),level=23) for i in range(5)]
        response=profile(charts,[score(c["id"]) for c in charts])
        self.assertEqual(response["reference"],REFERENCE)
        bucket=response["buckets"][2]
        self.assertGreater(bucket["rawEquivalentLevel"],23)
        self.assertTrue(all(c["score"]==1_000_000 and c["plate"]=="PG" and c["pumbility"]==380
                            for c in bucket["contributingCharts"]))
        self.assertGreater(calculate_phoenix2_pumbility("Single",23,995000,"UG"),
                           calculate_phoenix2_pumbility("Single",23,994999,"UG"))

    def test_bpm_calibration_anchors_precision_and_unchanged_220_plus(self):
        response=profile([],[])
        old_ceilings=(23.8,23.95,23.65,23.95,24.1,24.55,25.0,25.0,25.0)
        targets=(93,93,91,94,93,96,98,95,100)
        ceilings=(22.834,22.9735,22.4215,23.113,23.113,23.968,24.7,24.25,25.0)
        for bucket,old_ceiling,target,ceiling in zip(response["bpmBuckets"],old_ceilings,targets,ceilings):
            with self.subTest(bpm=bucket["label"]):
                self.assertEqual(bucket["radarScale"],{
                    "singles":{"minimumAlignedLevel":10.0,"maximumAlignedLevel":ceiling},
                    "doubles":{"minimumAlignedLevel":10.0,"maximumAlignedLevel":old_ceiling}})
                self.assertAlmostEqual(ceiling,10+(old_ceiling-10)*target/100)
                # These targets refer to method 4's calibrated, uncapped rating,
                # not the original common 10-to-25 scale or its rounded labels.
                self.assertAlmostEqual(100*(ceiling-10)/(old_ceiling-10),target)
                for key,mode_ceiling in (("singles",ceiling),("doubles",old_ceiling)):
                    self.assertAlmostEqual(uncapped_radar_score(10,bucket,key),0)
                    self.assertAlmostEqual(uncapped_radar_score(mode_ceiling,bucket,key),100)
                    self.assertAlmostEqual(uncapped_radar_score((10+mode_ceiling)/2,bucket,key),50)
                    self.assertLess(uncapped_radar_score(9,bucket,key),0)
                    self.assertGreater(uncapped_radar_score(mode_ceiling+.1,bucket,key),100)
                for aligned in (10,15.3789,20,25,28):
                    old_rating=100*(aligned-10)/(old_ceiling-10)
                    self.assertAlmostEqual(uncapped_radar_score(aligned,bucket,"singles"),old_rating*100/target)
                    self.assertEqual(uncapped_radar_score(aligned,bucket,"doubles"),old_rating)
                # A Doubles raw level is aligned once, not applied again to Singles.
                self.assertAlmostEqual(uncapped_radar_score((old_ceiling+1)-1,bucket,"doubles"),100)
                if bucket["id"]=="8":
                    for aligned in (0,10,15.37,23.95,25,28):
                        for key,_mode in MODES:
                            self.assertEqual(uncapped_radar_score(aligned,bucket,key),100*(aligned-10)/15)
        self.assertEqual(response["bpmBuckets"][1]["radarScale"]["singles"]["maximumAlignedLevel"],22.9735)
        # Returned metadata must not expose a mutable alias to the canonical scale.
        response["bpmBuckets"][1]["radarScale"]["singles"]["maximumAlignedLevel"]=99
        response["bpmBuckets"][1]["radarScale"]["doubles"]["maximumAlignedLevel"]=98
        self.assertEqual(BPM_BUCKETS[1]["radarScale"]["singles"]["maximumAlignedLevel"],22.9735)
        self.assertEqual(BPM_BUCKETS[1]["radarScale"]["doubles"]["maximumAlignedLevel"],23.95)

    def test_all_bpm_profile_anchors_align_doubles_once_and_clip_only_at_display(self):
        charts=[chart(f"{index}-{key}",mode=mode,bpm=bucket["min"] or 140)
            for index,bucket in enumerate(BPM_BUCKETS) for key,mode in MODES]
        rows=[score(c["id"]) for c in charts]
        aggregate=aggregate_bpm_progression(charts,rows)
        for anchor,adjustment,expected_rating in (("minimumAlignedLevel",0,0),
                ("minimumAlignedLevel",-.1,0),("maximumAlignedLevel",0,100)):
            with self.subTest(anchor=anchor,adjustment=adjustment):
                for index,bucket in enumerate(aggregate["buckets"]):
                    for key,offset in (("singles",0),("doubles",1)):
                        bucket[key]["equivalentLevel"]=BPM_BUCKETS[index]["radarScale"][key][anchor]+offset+adjustment
                with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
                    response=profile(charts,rows)
                for bucket in response["buckets"]:
                    self.assertAlmostEqual(bucket["radarScore"],expected_rating)
                    offset=1 if bucket["mode"]=="doubles" else 0
                    self.assertAlmostEqual(bucket["rawEquivalentLevel"]-offset,bucket["displayEquivalentLevel"])
        for index,bucket in enumerate(aggregate["buckets"]):
            for key,offset in (("singles",0),("doubles",1)):
                bucket[key]["equivalentLevel"]=BPM_BUCKETS[index]["radarScale"][key]["maximumAlignedLevel"]+.1+offset
        with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
            clipped=profile(charts,rows)
        self.assertTrue(all(bucket["radarScore"]==100 for bucket in clipped["buckets"]))
        self.assertTrue(all(uncapped_radar_score(bucket["displayEquivalentLevel"],BPM_BUCKETS[int(bucket["bpmBucketId"])],bucket["mode"])>100
            for bucket in clipped["buckets"]))

    def test_calibration_preserves_performance_contributions_and_suggestion_targets(self):
        charts=[chart(str(i),level=20) for i in range(6)]
        rows=[score(str(i),pumbility=350+i) for i in range(6)]
        calibrated=profile(charts,rows)
        with patch.dict(BPM_BUCKETS[1]["radarScale"]["singles"],maximumAlignedLevel=23.95):
            old_scale=profile(charts,rows)
        changed=calibrated["buckets"][2]
        previous=old_scale["buckets"][2]
        self.assertGreater(changed["radarScore"],previous["radarScore"])
        for key in ("rawEquivalentLevel","displayEquivalentLevel","contributingCharts",
                    "suggestedLevel","suggestions","usableCharts","selectedCharts"):
            self.assertEqual(changed[key],previous[key],key)

    def test_doubles_restoration_keeps_singles_and_performance_inputs_unchanged(self):
        charts=[chart(f"{key}{i}",mode=mode,level=20+offset)
            for key,mode,offset in (("s","Single",0),("d","Double",1)) for i in range(5)]
        rows=[score(c["id"]) for c in charts]
        restored=profile(charts,rows)
        with patch.dict(BPM_BUCKETS[1]["radarScale"]["doubles"],maximumAlignedLevel=22.9735):
            previous=profile(charts,rows)
        self.assertEqual(restored["buckets"][2]["radarScore"],previous["buckets"][2]["radarScore"])
        self.assertLess(restored["buckets"][3]["radarScore"],previous["buckets"][3]["radarScore"])
        for current,old in zip(restored["buckets"],previous["buckets"]):
            for key in ("rawEquivalentLevel","displayEquivalentLevel","contributingCharts",
                        "suggestedLevel","suggestions","usableCharts","selectedCharts","needsPlays"):
                self.assertEqual(current[key],old[key],(current["key"],key))

    def test_mode_specific_group_ranking_keeps_precision_after_visible_clipping(self):
        charts=[chart("s",level=25),chart("d",mode="Double",level=26)]
        rows=[score(c["id"]) for c in charts]
        aggregate=aggregate_bpm_progression(charts,rows)
        for single_aligned,double_aligned in ((24.0,24.1),(25.1,25.2)):
            aggregate["buckets"][1]["singles"]["equivalentLevel"]=single_aligned
            aggregate["buckets"][1]["doubles"]["equivalentLevel"]=double_aligned+1
            with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
                response=profile(charts,rows)
            self.assertGreater(double_aligned,single_aligned)
            self.assertEqual([response["buckets"][i]["radarScore"] for i in (2,3)],[100,100])
            self.assertEqual(response["groups"]["strengths"],["singles:1","doubles:1"])

    def test_normalized_grouping_uses_full_precision_beyond_visible_limits(self):
        charts=[chart(f"{bpm}-{i}",level=25,bpm=bpm) for bpm in (155,205) for i in range(5)]
        rows=[score(c["id"]) for c in charts]
        aggregate=aggregate_bpm_progression(charts,rows)
        for lower_raw,higher_raw in ((23.7,24.0),(25.5,26.0)):
            with self.subTest(raw_levels=(lower_raw,higher_raw)):
                aggregate["buckets"][1]["singles"]["equivalentLevel"]=lower_raw
                aggregate["buckets"][6]["singles"]["equivalentLevel"]=higher_raw
                with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
                    response=profile(charts,rows)
                self.assertEqual(response["groups"]["strengths"],["singles:1","singles:6"])
                if lower_raw>25:
                    self.assertEqual([response["buckets"][i]["radarScore"] for i in (2,12)],[100,100])

    def test_weaknesses_use_normalized_ranking_and_zero_evidence_ties_first(self):
        charts=[chart(f"{index}-{key}",mode=mode,level=20,bpm=bucket["min"] or 140)
            for index,bucket in enumerate(BPM_BUCKETS) for key,mode in (("s","Single"),("d","Double"))]
        rows=[score(c["id"]) for c in charts]
        aggregate=aggregate_bpm_progression(charts,rows)
        for bucket in aggregate["buckets"]:
            for key,offset in (("singles",0),("doubles",1)):
                bucket[key]["equivalentLevel"]=24+offset
        aggregate["buckets"][1]["singles"]["equivalentLevel"]=18
        aggregate["buckets"][6]["singles"]["equivalentLevel"]=18.5
        with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
            ranked=profile(charts,rows)
        self.assertEqual(ranked["groups"]["weaknesses"][:2],["singles:6","singles:1"])
        # Nonpositive equivalent evidence ties with absent evidence, before even
        # a measured value whose normalized rating is below the visible zero.
        aggregate["buckets"][0]["singles"]["equivalentLevel"]=3
        aggregate["buckets"][1]["singles"]["equivalentLevel"]=0
        aggregate["buckets"][0]["doubles"]["scoreCount"]=0
        aggregate["buckets"][0]["doubles"]["contributors"]=[]
        with patch("skill_radar.aggregate_bpm_progression",return_value=aggregate):
            missing=profile(charts,rows)
        self.assertEqual(missing["groups"]["weaknesses"],["doubles:0","singles:1","singles:0"])
        self.assertNotIn("singles:1",missing["groups"]["strengths"])

    def test_groups_use_uncapped_levels_and_keep_empty_buckets_weak(self):
        charts=[chart(f"{bpm}-{i}",level=27,bpm=bpm) for bpm in (195,205) for i in range(5)]
        rows=[score(c["id"],pumbility=450 if c["bpmMin"]==195 else 470) for c in charts]
        response=profile(charts,rows)
        self.assertEqual(response["groups"]["strengths"],["singles:6","singles:5"])
        self.assertEqual([response["buckets"][i]["radarScore"] for i in (10,12)],[100,100])
        self.assertEqual(response["groups"]["weaknesses"],["singles:0","doubles:0","singles:1"])

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

    def test_suggestions_prefer_ten_charts_in_starting_folder_sorted_by_pumbility(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("c"+str(i),level=19) for i in range(12)]
        candidates += [chart("cross",level=19,bpmMin=150,bpmMax=160),chart("wrong",level=20)]
        charts=earned+candidates
        ratings=[18.5]*5+[19.8,19.0,18.9,19.5,19.3,19.2,19.7,20.1,19.6,19.4,19.1,None]+[18.7,18.8]
        tiers=tiers_for(charts,ratings)
        catalog=build_suggestion_catalog(tiers,charts)
        response=build_skill_radar(charts,[score(c["id"]) for c in earned],catalog,player_key=KEY,
            display_name="PLAYER",generated_at_utc=STAMP,
            personal_scores=[{"chartId":"c1","score":999999,"plateCode":"PG"}])
        suggestions=response["buckets"][2]["suggestions"]
        self.assertEqual([row["chartId"] for row in suggestions],["c2","c1","c10","c5","c4","c9","c3","c8","c6","c0"])
        self.assertTrue(all(row["level"]==19 for row in suggestions))
        self.assertEqual(suggestions[1]["personalScore"],999999)
        self.assertEqual(suggestions[1]["personalPlate"],"PG")
        self.assertNotIn("cross",[row["chartId"] for row in catalog["charts"]])
        self.assertEqual(response["buckets"][0]["suggestions"],[])

    def test_suggestions_fill_higher_folders_then_sort_all_available_by_pumbility(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("base-a",level=19),chart("base-b",level=19),
            chart("next-a",level=20),chart("next-b",level=20),chart("later",level=21),
            chart("too-far",level=22),chart("other-mode",mode="Double",level=19),
            chart("other-bpm",level=19,bpm=165),chart("cross",level=19,bpmMin=150,bpmMax=160)]
        charts=earned+candidates
        response=build_skill_radar(charts,[score(c["id"]) for c in earned],
            build_suggestion_catalog(tiers_for(charts,[18.5]*5+[20,19.7,18.8,21.2,20.1,17,18,18,18]),charts),
            player_key=KEY,display_name="PLAYER",generated_at_utc=STAMP,
            personal_scores=[{"chartId":"next-a","score":990123,"plateCode":"SG"}])
        suggestions=response["buckets"][2]["suggestions"]
        self.assertEqual([c["chartId"] for c in suggestions],["too-far","next-a","base-b","base-a","later","next-b"])
        self.assertEqual([c["level"] for c in suggestions],[22,20,19,19,21,20])
        self.assertEqual((suggestions[1]["personalScore"],suggestions[1]["personalPlate"]),(990123,"SG"))

    def test_ten_suggestions_do_not_change_five_score_ratings_or_grouping(self):
        earned=[chart("e"+str(i),level=18) for i in range(7)]
        candidates=[chart("practice"+str(i),level=19) for i in range(12)]
        charts=earned+candidates
        rows=[score(c["id"]) for c in earned]
        with patch("skill_radar.SUGGESTION_LIMIT",5):
            previous=profile(charts,rows)
        current=profile(charts,rows)
        self.assertEqual(len(previous["buckets"][2]["suggestions"]),5)
        self.assertEqual(len(current["buckets"][2]["suggestions"]),10)
        self.assertEqual(current["buckets"][2]["selectedCharts"],5)
        for result in (previous,current):
            for bucket in result["buckets"]:
                bucket.pop("suggestions")
        self.assertEqual(previous,current)

    def test_suggestions_skip_empty_folders_and_return_only_existing_charts(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("first",level=21),chart("second",level=21),chart("third",level=23)]
        charts=earned+candidates
        bucket=profile(charts,[score(c["id"]) for c in earned],
            tiers_for(charts,[18.5]*5+[22,21.5,21.7]))["buckets"][2]
        self.assertEqual(bucket["suggestedLevel"],19)
        self.assertEqual([c["chartId"] for c in bucket["suggestions"]],["second","third","first"])
        self.assertEqual(profile(earned,[score(c["id"]) for c in earned])["buckets"][2]["suggestions"],[])

    def test_recommendations_truncate_after_adding_and_sort_by_calculated_rating(self):
        earned=[chart("e"+str(i),level=18) for i in range(5)]
        candidates=[chart("c"+str(i),level=22) for i in range(7)]+[chart("wrong",level=23)]
        rows=[score(c["id"],pumbility=365.6) for c in earned]
        bucket=profile(earned+candidates,rows,
            tiers_for(earned+candidates,[18.5]*5+[22.9,21.99999,22.0,22.2,22.1,22.5,23.0,21.5]))["buckets"][2]
        self.assertAlmostEqual(bucket["rawEquivalentLevel"],21.2,places=3)
        self.assertEqual(bucket["suggestedLevel"],22)
        self.assertEqual([c["chartId"] for c in bucket["suggestions"]],["wrong","c1","c2","c4","c3","c5","c0","c6"])
        for points, expected in ((360.29,22),(360.28,21)):
            boundary=profile(earned,[score(c["id"],pumbility=points) for c in earned])["buckets"][2]
            self.assertEqual(boundary["suggestedLevel"],expected)

    def test_all_level_skill_evidence_is_not_limited_to_suggestion_catalog(self):
        low=[chart("low"+str(i),level=12) for i in range(5)]
        rated=[chart("rated",level=16)]
        response=profile(low+rated,[score(c["id"]) for c in low],tiers_for(rated))
        bucket=response["buckets"][2]
        self.assertGreater(bucket["rawEquivalentLevel"],12)
        self.assertLess(bucket["rawEquivalentLevel"],12.1)
        self.assertEqual(bucket["selectedCharts"],5)
        self.assertEqual([row["chartId"] for row in bucket["suggestions"]],["rated"])
        self.assertEqual(bucket["suggestedLevel"],16)


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
        self.assertAlmostEqual(first["buckets"][2]["rawEquivalentLevel"],12.077125906394199)
        self.assertIs(first,read_skill_radar(store,KEY))
        store.values[recommendation_player_state_path(KEY)]={"playerId":"private-player",
            "lastSyncedAtUtc":"2026-10-19T00:00:00Z","scores":[score("0",pumbility=400)]}
        second=read_skill_radar(store,KEY)
        self.assertIsNot(first,second)
        self.assertGreater(second["buckets"][2]["rawEquivalentLevel"],first["buckets"][2]["rawEquivalentLevel"])
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

    def test_runtime_cache_identity_includes_the_actual_bpm_calibration(self):
        store=Store()
        first=read_skill_radar(store,KEY)
        with patch.dict(BPM_BUCKETS[1]["radarScale"]["singles"],maximumAlignedLevel=25.0):
            changed=read_skill_radar(store,KEY)
        self.assertIsNot(first,changed)
        self.assertNotEqual(first["buckets"][2]["radarScore"],changed["buckets"][2]["radarScore"])
        self.assertEqual(first["buckets"][2]["rawEquivalentLevel"],changed["buckets"][2]["rawEquivalentLevel"])

    def test_method_and_profile_version_changes_invalidate_cached_profiles(self):
        store=Store()
        with patch("skill_radar_runtime.METHOD_VERSION",5), patch("skill_radar_runtime.PROFILE_VERSION",10):
            old_revision=read_skill_radar(store,KEY)
            self.assertIs(old_revision,read_skill_radar(store,KEY))
        current=read_skill_radar(store,KEY)
        self.assertIsNot(old_revision,current)
        self.assertIs(current,read_skill_radar(store,KEY))
        self.assertEqual(current["methodVersion"],6)

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
            self.assertEqual(report["methodVersion"],METHOD_VERSION)
            self.assertEqual(saved["reference"],REFERENCE)
            self.assertAlmostEqual(saved["buckets"][2]["rawEquivalentLevel"],12.077125906394199)
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
