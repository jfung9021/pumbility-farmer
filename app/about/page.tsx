"use client";

import Link from "next/link";
import { SiteHeader } from "../_components/site-header";
import { ScoreSyncLink } from "../_components/score-sync-link";
import { usePlayerProfile } from "../_components/player-profile-provider";
import { MINIMUM_OFFICIAL_SCORES } from "../../lib/official-comparison";
import styles from "./about.module.css";

const contents = [
  ["score-sync", "Getting started"],
  ["recommendations", "Recommendations"],
  ["skill-ratings", "Your skill ratings"],
  ["tier-lists", "Tier Lists"],
  ["tier-bands", "Reading tier bands"],
  ["scoring", "Scoring methods"],
  ["clearing", "Clearing methods"],
  ["pumbility", "Pumbility methods"],
  ["coop", "Co-op"],
  ["skill-radar", "Skill Radar"],
  ["player-comparison", "Player Comparison"],
  ["data", "Data & privacy"],
] as const;

export default function AboutPage() {
  const { profileHref } = usePlayerProfile();
  return (
    <main className={styles.page}>
      <SiteHeader active="about" />
      <header className={styles.hero}>
        <p className={styles.eyebrow}>Pumbility Farmer / Field guide</p>
        <h1>About the numbers</h1>
        <p>Find your next chart, understand the estimates, and see how your scores become a picture of your progress.</p>
      </header>
      <div className={styles.layout}>
        <nav className={styles.toc} aria-label="On this page">
          <h2>On this page</h2>
          <ul>{contents.map(([id, label]) => <li key={id}><Link href={`#${id}`}>{label}</Link></li>)}</ul>
        </nav>
        <div className={styles.content}>
          <section className={styles.section} id="score-sync" aria-labelledby="score-sync-title">
            <h2 id="score-sync-title">Get your scores ready</h2>
            <ol>
              <li>Log in to PIU Scores and make your account public so your scores can pass through the API.</li>
              <li>Open the Phoenix score upload page and sync your latest scores.</li>
              <li>Return here, choose your player from Menu, and view recommendations based on the updated history.</li>
            </ol>
            <ScoreSyncLink>Open PIU Scores upload</ScoreSyncLink>
          </section>

          <section className={styles.section} id="recommendations" aria-labelledby="recommendations-title">
            <h2 id="recommendations-title">Recommendations</h2>
            <p>Choose your player from Menu, see which Phoenix version supplies your skill rating, and rank charts against your current Phoenix 2 history. Recommendations also show your Top 50 scores. Tier Lists can show your saved scores and plates for the selected mode.</p>
            <h3 id="skill-ratings">Your skill ratings</h3>
            <dl className={styles.metricList}>
              <div><dt>Scoring skill</dt><dd>Your top-20 Pumbility average, converted to S + Fair Game difficulty. Manually entered scoring skill is used for manual profiles.</dd></div>
              <div><dt>Clearing skill</dt><dd>The average current official difficulty of your 50 hardest unique clears, separately for Singles and Doubles. You need 50 unique clears; repeat clears and charts cleared in both Phoenix versions count once. A manual profile needs a player clear history before this can be calculated.</dd></div>
            </dl>
            <p>A clearing rating can be unavailable until enough unique clears are recorded, or appear as “Not yet calculated” until the player&apos;s ratings are refreshed. These personal ratings retain their own definition even when a tier method uses a different history requirement.</p>
            <div className={styles.sectionLinks}><Link href={profileHref("/recommendations")}>Open Recommendations ↗</Link></div>
          </section>

          <section className={styles.section} id="tier-lists" aria-labelledby="tier-lists-title">
            <h2 id="tier-lists-title">Tier Lists</h2>
            <p>Compare Scoring, Clearing, and Pumbility estimates in compact or detailed views, using Phoenix 1 and Phoenix 2 scores. The selected scoring goal and published generation determine the method. The descriptions below cover current methods, earlier generations, and labeled local experiments; chart details identify the calculation and evidence used for each chart.</p>
            <dl className={styles.metricList}>
              <div><dt>Scoring</dt><dd>How difficult it is to achieve scores, or the selected SSS / SSS+ target, in the observed best-score records.</dd></div>
              <div><dt>Clearing</dt><dd>Difficulty inferred from the skills of successful players.</dd></div>
              <div><dt>Pumbility</dt><dd>A composite of the displayed Scoring and Clearing estimates, using the published generation&apos;s aggregation policy.</dd></div>
            </dl>
            <h3 id="tier-bands">Reading tier bands</h3>
            <p>Within each band, charts are sorted by estimated difficulty − (official level + 0.5), from smallest difference to largest. The folder reference is the official midpoint: an S20 folder anchors at 20.5. Estimates can cross official levels, so an S20 can receive 19.2.</p>
            <h3>Midpoint adjustment and evidence</h3>
            <p>For generations using midpoint compression, the initial Scoring and Clearing distances from official level + 0.5 are halved once. The displayed values follow the published generation&apos;s adjustment policy. Pumbility then averages the adjusted components or takes their maximum according to its recorded policy; no extra compression is applied to the composite.</p>
            <p>Limited, provisional, and Unrated describe different evidence conditions. Missing records are unknown. Observed successful best scores are not attempt logs and cannot isolate practice effort or establish first-attempt pass probabilities. Chart details summarize the records used and any data limitations, including support counts and available intervals. The method notes below retain the detailed calculation descriptions.</p>
            <p>In generations with configured chart-version exclusions, those exclusions apply before score merging, player histories, and fitting. Final SSS+ Scoring at level 20+ and official D26+ Clearing can expand only narrow sides of their final displayed spread, preserving supported folder medians and allowing cross-level outliers. Four-chart folder calibration remains provisional.</p>
            <p>Labeled local experiments may use Phoenix 2 data only or average Scoring and Clearing at full precision. Both components are required for the local average.</p>
            <div className={styles.sectionLinks}><Link href={profileHref("/tier-list")}>Open Tier Lists ↗</Link><Link href="#scoring">Scoring</Link><Link href="#clearing">Clearing</Link><Link href="#pumbility">Pumbility</Link></div>
          </section>

          <section className={styles.section} id="scoring" aria-labelledby="scoring-title">
            <h2 id="scoring-title">Scoring methods</h2>
            <h3>SSS and SSS+ target attainment</h3>
            <p>The inclusive targets are 990,000 for SSS and 995,000 for SSS+. A regularized logistic model fits separate player and chart effects to successful recorded best scores. Players need four observed eligible charts, and their total observation weights are balanced so longer histories do not dominate. Missing scores and broken records add no failures.</p>
            <p>Submitted and official cohorts are fitted and calibrated independently for Singles and Doubles. The submitted model uses valid level-16+ histories and supplies levels 16–19. The official model uses merged level-20+ leaderboards and supplies 20+. Official Phoenix versions are matched by exact full game tag, retaining the higher score. Submitted and official identities are never joined by display name.</p>
            <p>Each source uses supported folder medians as anchors at official level + 0.5, joined by exponential segments with labeled boundary tangent extrapolation. A reference chart needs 20 usable contributors, at least five observed outcomes on each side of the target, and comparable metadata. A reference folder needs five charts; calibration needs at least three increasing anchors including the source&apos;s lowest level. Initial estimates and intervals receive the fixed 50% midpoint adjustment once.</p>
            <p>First-pass charts need both outcomes, five usable contributors, a calibrated comparison component, and a stable converged fit. Sparse outcomes, weak information, truncated boards, comparability issues, and extrapolation are provisional. Generations without refinement leave one-sided outcomes Unrated.</p>
            <h4>Boundary and rare-success refinements</h4>
            <p>A refined generation freezes the first-pass rated range and classifies the complete available board before the history filter. Boards with no target successes extend the upper edge using the score shortfall to the target. Depending on the generation, this uses the highest score or the average of the best three available scores, using fewer if necessary. Boards with all target successes extend the lower edge using the lowest returned score&apos;s surplus. Held-out mixed-chart calibration converts score gaps into latent target units. Sparse folders borrow range offsets within the same source and mode; unsupported calibration stays Unrated.</p>
            <p>Earlier rare-success refinement uses a conditional chart-only fit for one to four target successes with matching full-board and usable evidence. It orders them within the highest 5% of the frozen numeric range and blends by success count, usable support, and success rate: one success among 20+ usable players gets full weight, while one among two does not. Player abilities, first-pass ranges, and anchors stay fixed.</p>
            <p>Continuous refinement at official level 20+ instead estimates rare-success chart effects from observed player abilities and outcomes with a folder prior. Prior spread comes from the same source and mode; unsupported cases use the frozen model&apos;s regularization. These estimates replace preliminary upper-band allocation before final calibration. Submitted levels below 20 retain their recorded refinement. Boundary and rare-success corrections remain provisional, receive midpoint adjustment once, and have no formal interval.</p>
            <h4>Folder centering and final median calibration</h4>
            <p>In folder-centered generations, an official folder needs five finite mixed first-pass charts with a positive range and five rated final charts for its own centering offset. All finite final estimates, including boundary and rare-success results, set the median at official level + 0.5. Sparse folders interpolate or borrow the nearest offset within their source and mode, so their own median may differ. The fixed offset precedes the one midpoint compression and moves effective ranges and existing intervals equally. The original fit values and frozen edges remain part of the calculation provenance. Pumbility is averaged afterward without independent centering.</p>
            <p>Final median-calibrated generations anchor each supported level-20+ folder&apos;s median refined chart effect at level + 0.5. An exponential curve recalibrates spacing across levels, followed by a residual median offset, one 50% midpoint adjustment, the applicable continuous spread adjustment, and the tail policy. Derived anchors remain provisional; preliminary diagnostics and recalibrated values remain part of the calculation provenance.</p>
            <h4>Level-20 scoring spread</h4>
            <p>S20 and D20 ratings aim to keep most charts near their official level, while allowing unusually easy or hard charts outside it. This final width policy is part of the published generation; it does not change which records were observed.</p>
            <h4>Spread and tail policies</h4>
            <p>The ordinary tail policy leaves distances within ±1.5 of the midpoint unchanged and tapers larger distances toward ±1.9. An exception needs a supported, uncapped, mixed-outcome first pass with 20 usable players, five outcomes on each side, supported local calibration, no extrapolation, a stable fit, and a bootstrap interval wholly beyond ±2. Only eligible outer tails qualify. Ties and median-defining charts are protected, and at most ten charts across both modes and all levels retain distances up to ±2.5. One-sided, rare-corrected, capped, or unsupported charts cannot qualify from a score gap alone.</p>
            <p>Continuous-spread generations apply a scale-0.55 asinh curve after midpoint compression to draw distant charts toward the center while retaining continuous separation. This encourages concentration without assuming a normal distribution or a fixed count in each band. The ordinary tail safeguard follows. A bounded exception path can retain stronger outliers, including qualifying capped one-sided boards with explicit support and sensitivity checks. Exceptions share a global budget of ten while protecting chart ordering, ties, and supported folder medians. Capped boards stay provisional; score surplus does not establish population attainment. These are rating-scale policies, not extra statistical evidence.</p>
            <h4>Submitted D16–D19 SSS+ calibration</h4>
            <p>This variant retains the shared-player fit across all eligible submitted Double 16+ histories. Each eligible lower folder centers its median chart effect at level + 0.5. The median of the folders&apos; 90th-percentile absolute effect residuals supplies one common scale. An initial multiplier of 0.8 is followed by one 50% midpoint adjustment, the scale-0.55 asinh spread, fixed final width, and the ordinary tail safeguard. No new fit or score pull is performed. These folder-relative estimates remain provisional and cannot separate practice effort from difficulty.</p>
            <p>Target-model 95% intervals resample whole players and refit with calibration fixed. When applicable, endpoints pass through the selected chart&apos;s fixed tail map and exception decision. They exclude anchor uncertainty, effort, selective observation, and missing attempts. Boundary and rare-success corrections have no formal interval. General retains its own Scoring algorithm and Pumbility list; missing target estimates are not replaced by General or a historical target.</p>

            <h3>General: official cross-level Scoring, S20+ / D20+</h3>
            <p>Phoenix 1 and Phoenix 2 official boards merge by chart ID and exact full game tag, keeping each matched player/chart&apos;s higher raw official score. Unresolved identities stay separate and renamed tags may not match. Submitted and supplemented scores are excluded.</p>
            <p>Each mode is fitted across eligible levels. Players need four distinct eligible charts; shared-player comparisons separate player strength from chart cost. Missing appearances never count as failures. Only the largest connected comparison group shares a calibrated coordinate; disconnected charts are Unrated.</p>
            <p>Each comparable reference chart needs ten contributing players and each folder anchor needs five charts. Median fitted folder costs map to official level + 0.5. Exponential segments join exact median anchors; the curve is inverted after averaging score-point evidence. Score-point differences per level can grow at higher levels. This is not an average of individually transformed player ratings, and chart costs and difficulty use different units.</p>
            <p>Unsupported folders and tangent extensions are provisional. Five to nine contributors are provisional; fewer than five are Unrated. Changed or unknown note counts cannot set anchors. Conditional 95% intervals resample player evidence 1,000 times with fitted effects and median anchors fixed. They exclude model fit, identity, comparability, effort, selection, and truncated-board uncertainty. Each mix has its own source date.</p>
            <p>Hybrid generations retain submitted score-profile Scoring at levels 16–19; missing official evidence at 20+ never selects that fallback. Earlier cross-level-only generations leave Scoring and Pumbility below level 20 Unrated.</p>

            <h3>General: submitted score profiles</h3>
            <p>Hybrid levels 16–19 and earlier submitted generations use one equally weighted score per successful player. Phoenix 1 scores normalize to the current chart note count, and Phoenix 2 replaces overlapping records. Player skill, Pumbility, and top/recent windows do not select or weight this sample.</p>
            <p>The upper-profile version uses linearly interpolated 50th-, 75th-, and 90th-percentile scores; the earlier version adds the 10th and 25th percentiles. These match a continuous reference curve separately for Singles and Doubles. The 90th-percentile squared difference has double weight; every other percentile has weight one.</p>
            <p>Initial difficulty is official level + 0.5 + folder scale × (profile match − the folder&apos;s median profile match), followed by the generation&apos;s midpoint adjustment. Matching another folder&apos;s typical profile does not force that folder&apos;s midpoint. Each mode and official level has its own spread scale. Scales seek useful spreads while staying close to neighboring levels; sparse folders borrow support. Narrow folders aim for about one grade across their middle 80%, while broader folders can retain wider spreads.</p>
            <p>The profile calibration treats roughly 3–10 two-grade moves across both modes as a guideline, with a soft penalty beyond ten, no minimum quota, and no hard cap. Limited-data charts and both directions count: an S21 estimate of 23.0 or above, or below 20.0, qualifies.</p>
            <p>Reference folders need five charts with 20+ successful players each. Raw matches outside the reference range extend linearly and are marked before final calibration. Lower profile match error means a closer fit. The record warning appears below 10 players; provisional model evidence is shown separately. Intervals bootstrap the entire profile 1,000 times with reference curve, folder center, and selected folder scale fixed, excluding scale-selection uncertainty. They describe observed best-score populations; charts mostly played by strong players can receive lower estimates.</p>

            <h3>Earlier official within-folder Scoring, S23+ / D25+</h3>
            <p>This version compares each player&apos;s best official Phoenix 2 scores within one mode and official level. A normal comparison needs three other charts. Shared players adjust for chart selection, with the target excluded from its baseline. A lower-than-expected score implies greater difficulty; missing entries add no failures.</p>
            <p>Every player supplies one equally weighted estimate. Their full-precision mean uses a shared linear coefficient per level and a median anchor at x.5. The coefficient is min(1, 0.45 / the 90th percentile of absolute chart offsets), fitted from eight charts with ten eligible players and normal comparisons. It targets the central majority without stretching narrow distributions or limiting tails. Sparse levels borrow the nearest supported same-mode scale, or use 0.4 without a donor. Outlier labels do not alter estimates.</p>
            <p>Small folders and those without normal panels can use fewer comparisons and are provisional. Disconnected groups have separate median anchors and cannot be compared reliably. No same-level comparison means Unrated. Fewer than ten players or provisional comparisons give limited evidence. Intervals bootstrap players 1,000 times with adjustments and calibration fixed, excluding effort, selection, and hidden scores below the 300-score cap. Below S23 / D25, the submitted score population continues to apply.</p>

            <h3>Legacy scoring generations</h3>
            <p>Legacy scoring compares player performance within official folders, using both Phoenix sources with their original normalization and equal observation weights, or Phoenix 2 alone in labeled experiments. Legacy and new Phoenix 2 charts share one model per mode. Folder ranks, confidence intervals, and official-level What-if values describe that model. Official level + 0.5 is the reference before shrinkage; the final median can differ slightly. The record warning appears below 10 scoring contributors.</p>
          </section>

          <section className={styles.section} id="clearing" aria-labelledby="clearing-title">
            <h2 id="clearing-title">Clearing methods</h2>
            <h3>Submitted cross-level and hybrid Clearing</h3>
            <p>Player skill averages the current official levels of the 30 hardest unique clears. Players need the published minimum number of distinct valid score records in the same mode across both Phoenix versions: current generations use 100 and earlier generations use 200. Duplicates count once; history below level 16 contributes. These tier requirements do not change personal skill ratings.</p>
            <p>A chart uses the linearly interpolated 10th percentile of qualified clearers&apos; skills. Reference charts need 20 qualified clearers and comparable metadata; each supported folder needs five reference charts. Median folder thresholds map to official level + 0.5.</p>
            <p>The original cross-level version fits one piecewise-linear curve per mode across levels 16+, with linear boundary extrapolation. The hybrid version fits its curve using only level-20+ anchors for 20+ charts. At levels 16–19, initial difficulty is official midpoint + 0.70 × (chart threshold − eligible folder median threshold). No supported lower-folder reference means Unrated. All levels still contribute to skill and qualification.</p>
            <p>Fewer than 20 qualified clearers, unsupported folders, changed or unknown metadata, and extrapolation receive weaker evidence. No usable skills means Unrated. These are successful best-record observations, not attempt logs; missing records are unknown and no pass probability or confidence interval is inferred. Submitted source snapshot dates remain part of chart evidence.</p>

            <h3>Official D26+ Clearing</h3>
            <p>Official clearer ability replaces submitted Clearing at D26 and above in this variant; Singles and Doubles through D25 retain submitted Clearing. A player&apos;s ability averages the official midpoints of their 25 hardest other unique D23+ clears, requiring 25 other clears and excluding the target. A directly estimated chart uses the interpolated 20th percentile of eligible abilities.</p>
            <p>The evidence weight is n / (n + 20) × n / N, where n is eligible and N observed clearers. Raw offset is 0.70 × weight × (chart threshold − folder median threshold). Robust folder calibration needs five charts with ten eligible clearers and targets a 90th-percentile absolute offset of 0.45; sparse folders borrow a supported same-mode scale. Finite estimates recenter to the actual folder median at level + 0.5, then compress by 50% once.</p>
            <p>Source variants use Phoenix 2 only or combined Phoenix 1 + Phoenix 2 histories and target boards. Combined records match full game tags and retain the higher score per player/chart. In the earlier combined variant, either source&apos;s truncation flag triggers the cap fallback; a merged count of 300 or more alone does not prove truncation.</p>
            <p>In the observed-Phoenix-1 variant, Phoenix 1 cap flags are informational. Those charts use their observed clearer skills, contribute to calibration, and can receive conditional bootstrap intervals. Only Phoenix 2 cap flags trigger fallback. Intervals describe observed records and exclude selection uncertainty. The published generation records the source cutoffs, dates, and active policy.</p>
            <p>Boards subject to fallback receive the folder&apos;s lowest directly estimated difficulty, or its midpoint if none exists. Conditional bootstrap intervals keep player histories and folder calibration fixed. Cap-default assignments are provisional and have no formal interval.</p>

            <h3>Earlier official clearer ability, S25+ / D26+</h3>
            <p>Official S22+ / D23+ boards supply histories, separately for Singles and Doubles. Ability is the average official level + 0.5 of the player&apos;s 25 hardest other distinct clears, requiring 25 and excluding the target. Each uncapped chart uses the interpolated 20th percentile of eligible abilities. The raw difference is 0.70 × evidence weight × (chart percentile − folder median percentile), referenced to uncapped charts with usable histories in that folder.</p>
            <p>Evidence weight is n / (n + 20) × n / N. Small or incomplete samples pull toward the midpoint instead of making an unpopular chart harder. Fewer than 20 eligible clearers means Insufficient evidence; none means Unrated. The record warning appears below 10 valid records. Bootstrap intervals hold histories fixed and describe observed ability samples, excluding preferences and missing attempts.</p>
            <p>Calibration is 0.45 divided by the 90th percentile of absolute evidence-weighted offsets among uncapped charts with ten eligible clearers. This targets roughly 90% of the reference distribution within level + 0.05 to level + 0.95, the displayed x.0–x.9 bands, separately per official level. It does not promise exactly 90% of all charts. Five supported reference charts are required; otherwise the nearest supported same-mode scale is borrowed. No donor or zero spread keeps coefficient 1. Tails are unbounded.</p>
            <p>Outlier labels require the raw difference and its entire ability interval to pass three interquartile ranges, with 20 eligible clearers and half of observed players represented. Labels never gate or clip estimates. Level crossings and limited evidence are reported separately.</p>
            <p>A full 300-score board may omit lower scores, so it receives the lowest uncapped, nonempty estimate in its mode/folder, or the midpoint if none exists. That provisional fallback is identified in chart details and does not estimate lower-tail ability. Its observed players still contribute to histories. In this older generation, the original submitted method applies through S24 / D25.</p>

            <h3>Original submitted Clearing</h3>
            <p>Player clearing skill averages the current official difficulty of the 50 hardest unique clears, requiring 50 separately per mode. Repeat clears and charts cleared in both Phoenix versions count once. Each chart uses unique successful players from either Phoenix version, or Phoenix 2 only in labeled experiments. Players without the required clearing skill are excluded.</p>
            <p>A point-percentile generation uses its published single clearing-skill percentile, linearly interpolated across eligible clearers. An interval generation averages skills in its published percentile interval, including boundaries and ties. The selected percentile or interval follows the published generation&apos;s method.</p>
            <p>The median chart in each official folder anchors at level + 0.5. Each skill point from the folder reference changes difficulty by the generation&apos;s published difficulty-delta scale. Calibration always uses the chart&apos;s official folder, but estimates can cross levels. Sparse charts retain evidence and limited-data labels; no usable estimate means Unrated. This measures observed successful-player difficulty, not pass probability or first-clear ability; a player&apos;s skill may change after the clear.</p>
          </section>

          <section className={styles.section} id="pumbility" aria-labelledby="pumbility-title">
            <h2 id="pumbility-title">Pumbility methods</h2>
            <p>Both Scoring and Clearing must be available. Calculations use full precision before one-decimal display truncation. Evidence follows the weaker component, and each component&apos;s support is shown separately.</p>
            <dl className={styles.metricList}>
              <div><dt>SSS / SSS+ Pumbility</dt><dd>The arithmetic mean of the selected target Scoring estimate, after applicable calibration and tail policy, and selected Clearing. Levels 16–19 use the submitted target model and folder-centered Clearing. Levels 20+ use the independent official target model with submitted Clearing calibrated on 20+ anchors, except official D26+ variants. Missing target estimates never fall back to General or historical SSS.</dd></div>
              <div><dt>Adjusted-average generations and local experiments</dt><dd>Average the displayed Scoring and Clearing difficulties after midpoint adjustment, with no extra compression.</dd></div>
              <div><dt>Maximum generations</dt><dd>Take the higher of Scoring and Clearing. Historical average generations retain their recorded arithmetic-mean policy. A recognized aggregation policy is required.</dd></div>
            </dl>
            <p>Hybrid General combines submitted score profiles and folder-centered Clearing at levels 16–19. At 20+ it combines official cross-level Scoring with Clearing calibrated on 20+ anchors, using combined-source or Phoenix-2-only official clearer ability for D26+ when enabled. Components agree with their respective tabs. Cross-level-only generations leave Scoring and Pumbility below level 20 Unrated.</p>
            <p>Older official generations combine within-folder official Scoring at S23+ / D25+ with submitted Clearing through S24 / D25 and official ability at S25+ / D26+. Only those higher official Clearing tiers use their capped-board default.</p>
            <p>The record warning appears below 10 valid records in either displayed component, using the selected Scoring target and rated or selected Clearing records. Insufficient and provisional model evidence are shown separately. Target boundary and rare-success corrections remain provisional with no formal component or composite interval. Unrefined one-sided outcomes or unsupported calibration are Unrated. Extrapolated composites remain provisional.</p>
          </section>

          <section className={styles.section} id="coop" aria-labelledby="coop-title">
            <h2 id="coop-title">Co-op</h2>
            <p>Co-op charts share one 2x–5x tier list. Miss points adjust for player strength and Phoenix source using all observations, then a conditional 75th-percentile score is estimated for a median-strength Phoenix 2 player. The conditional quantile supplies robustness; raw scores and residuals are not trimmed.</p>
            <p>The ordering anchors the easiest chart at continuous difficulty 10, the median at 16, and the hardest at 24.9. Published difficulty truncates to whole numbers from 10 through 24, preserving observed order without forcing a normal distribution. Co-op recommendation letter-grade goals use these whole-number difficulties.</p>
          </section>

          <section className={styles.section} id="skill-radar" aria-labelledby="skill-radar-title">
            <h2 id="skill-radar-title">Skill Radar</h2>
            <p>View your personal Singles and Doubles BPM profile and find your next charts. Each BPM range averages up to five of your highest Phoenix 2 chart Pumbility values. Missing charts are excluded, ranges with fewer than five have an asterisk, and empty ranges show zero.</p>
            <p>UG is the equivalent level for a 995,000+ (SSS+) score with an Ultimate Game plate. Actual scores, plates, and earned Pumbility stay intact. S10 / D11 maps to zero. Singles and Doubles have independent 100-point ceilings for each BPM range; 220+ BPM keeps S25 / D26. Strengths and weaknesses use normalized ratings before clipping. Hiding a line changes only the drawing.</p>
            <p>Suggestions start at your equivalent level +1.5, rounded down to an official folder and starting at level 16. Up to ten charts are selected, filling from higher folders as needed, then sorted by SSS+ Pumbility difficulty. Five are shown initially; More reveals the remaining suggestions. Played charts remain eligible. Variable-tempo songs use their chosen focal BPM for categories and suggestions; chart labels retain the complete BPM range.</p>
            <div className={styles.sectionLinks}><Link href={profileHref("/skill-radar")}>Open Skill Radar ↗</Link></div>
          </section>

          <section className={styles.section} id="player-comparison" aria-labelledby="player-comparison-title">
            <h2 id="player-comparison-title">Player Comparison</h2>
            <p>Compare two players&apos; estimated BPM skills and official scores across every S20+ and D20+ chart. Players need {MINIMUM_OFFICIAL_SCORES} distinct official chart scores across Singles and Doubles to appear in the player lists. All comparison charts count, including those without BPM metadata.</p>
            <p>Official Phoenix 1 and Phoenix 2 scores match by player and chart, retaining the higher score. These are captured leaderboard entries: -- means no captured official score. Grades come from the score. Superb Game is assumed below 1,000,000 and Perfect Game at exactly 1,000,000. Plates and Pumbility are estimates.</p>
            <p>Each BPM bucket uses its five highest estimated Pumbility values, padding unrecorded slots with zero. Estimated PG level expresses equivalent Perfect Game reference performance; Doubles aligns by one level to Singles. Both radars use the same 0–100 scale, PG10 to PG25.</p>
            <p>The BPM comparison uses full aligned PG estimates before clipping or rounding. Higher estimates may reflect different chart coverage; fewer than five contributors is marked Limited. A tie means identical estimated PG.</p>
            <p>Charts enter the radar according to their chosen focal BPM. Displayed chart BPMs retain the complete range. All charts remain in the score list, including those without BPM metadata or captured boards. The contributing-chart toggle and folder filters are independent; folder filters affect only the score list. Missing scores never count as losses. Each source&apos;s snapshot date remains visible on the comparison page.</p>
            <div className={styles.sectionLinks}><Link href="/official-leaderboard-comparison">Open Player Comparison ↗</Link></div>
          </section>

          <section className={styles.section} id="data" aria-labelledby="data-title">
            <h2 id="data-title">Data and privacy</h2>
            <p>Built from consented score data. Top 50 display scores are public; internal player IDs and complete raw score histories stay private.</p>
            <p>Phoenix 2 is authoritative for every recommendation chart and overlapping score. Tier methods distinguish submitted records from official leaderboard sources as described above. Source dates and evidence describe the selected dataset; recorded best scores are incomplete observations of play.</p>
          </section>
        </div>
      </div>
    </main>
  );
}
