# Local average Pumbility experiment

Status: implemented and enabled locally. Production continues to use the maximum.

All 90 frontend tests, TypeScript, the production build, actual local API arithmetic/preservation checks, and the browser label, component, and Only 20+ checks pass. One bounded review found no remaining implementation regression. The separate schema-45 candidate has 1,306 rated Pumbility charts; the original schema-44 artifact is retained. Evidence is under `.local-data/pumbility-average-experiment/`.

Use Pumbility = (Scoring + Clearing) / 2 at full precision, after the existing
midpoint compression. No additional compression applies to Pumbility.

Equivalent delta form: for official level L, use midpoint M = L + 0.5 and the final, already compressed
Scoring S and Clearing C estimates:

    scoringDelta = S - M
    clearingDelta = C - M
    pumbilityDelta = (scoringDelta + clearingDelta) / 2
    Pumbility = M + pumbilityDelta

This equals (S + C) / 2. For S21 with S=20.9 and C=23.0, the deltas are -0.6 and
+1.5, giving +0.45 and Pumbility 21.95 (displayed S21.9). The proposed +2.1 is the
distance between the two component values, not the second value's midpoint delta.

Implementation:

1. Add a schema-45 local experiment derived from the existing validated schema-44
   aggregate. Keep the maximum baseline and its method identity for exact recovery
   and validation. Store explicit arithmetic-average policy and full-precision values.
2. Change only Singles/Doubles Pumbility estimates, deltas, bands, initial-fit
   composite values, and folder ranks. Preserve evidence/support, Scoring,
   Clearing, source provenance, Co-op, recommendations, and personal data. Require
   both components; missing values stay unrated, including current below-20 charts.
   Apply no additional compression. Repeating the conversion is idempotent.
3. Save the candidate separately under the ignored local experiments directory.
   Select it only with PIU_LOCAL_PUMBILITY_EXPERIMENT=average while local
   analysis mode is enabled. The normal aggregate and production defaults remain
   maximum. The production Python writer rejects this experiment schema.
4. Add exact experiment-policy validation, restore and validate the original
   maximum contract, and validate the derived composite values and ranks.
5. Label the local experiment and midpoint-delta formula on the Pumbility page.
   Keep existing truncation, filters, and component details.
6. Test mixed-sign and equal deltas, missing components, rank changes, idempotence,
   preservation, malformed policy/value rejection, and rollback. Generate the local
   candidate, run frontend tests, TypeScript and a production build, then open and
   verify the local Pumbility page. Do one bounded review; repair only proven issues.

The separately authorized production official-source capture and data refresh
continue using the maximum policy. Do not push or deploy this experiment.

Regenerate with `node --experimental-strip-types scripts/build_local_pumbility_experiment.ts`. The ignored `.env.local` selects `PIU_LOCAL_PUMBILITY_EXPERIMENT=average`. Remove that setting and restart the local server to restore the retained maximum artifact.
