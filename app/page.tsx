"use client";

import Link from "next/link";
import { ExplanatorySection } from "./_components/explanatory-section";
import { ScoreSyncLink } from "./_components/score-sync-link";
import { SiteHeader } from "./_components/site-header";
import { usePlayerProfile } from "./_components/player-profile-provider";
import styles from "./_components/player-shell.module.css";

export default function HomePage() {
  const { player, hydrated, loading, setupDismissed, openChooser, clearPlayer, profileHref } = usePlayerProfile();
  return (
    <main className="home-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <SiteHeader />

      <section className="home-hero">
        {hydrated && !loading && (player || !setupDismissed) ? <section className={styles.homeSetup} aria-label="Get started">
          <div><h1>{player ? `Welcome back, ${player.displayName}` : "Find your next chart"}</h1>
            <p>{player ? "Your player is ready across Recommendations, Tier Lists and Skill Radar. Manage your player in Menu." : "Save a player in this browser for recommendations, skill radar and personal scores."}</p>
          </div>
          <div className={styles.inlineActions}>
            {player ? <Link className={styles.primaryButton} href={profileHref("/recommendations")}>Continue</Link> : <>
              <button type="button" className={styles.primaryButton} onClick={openChooser}>Choose player</button>
              <Link className={styles.secondaryButton} href="/tier-list" onClick={clearPlayer}>Browse without a player</Link>
            </>}
          </div>
        </section> : null}
        <div className="feature-grid">
          <div className="home-feature">
            <Link className="feature-card feature-recommendations" href={profileHref("/recommendations")}>
              <div>
                <p>PERSONALIZED ROUTE</p>
                <h2>Recommendations</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About Recommendations">
              <p>
                Choose your player from Menu, see which version supplies your skill rating,
                and rank charts against your current Phoenix 2 history.
              </p>
            </ExplanatorySection>
          </div>

          <div className="home-feature">
            <Link className="feature-card feature-tier" href={profileHref("/tier-list")}>
              <div>
                <p>GLOBAL ANALYSIS</p>
                <h2>Tier Lists</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About Tier Lists">
              <p>
                Compare Scoring, Clearing, and Pumbility difficulty estimates
                in compact or detailed views, using Phoenix 1 and Phoenix 2 scores.
              </p>
            </ExplanatorySection>
          </div>
          <div className="home-feature">
            <Link className="feature-card feature-radar" href={profileHref("/skill-radar")}>
              <div>
                <p>YOUR BPM PROFILE</p>
                <h2>Skill Radar</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About Skill Radar">
              <p>View your personal Singles and Doubles BPM profile and find your next charts.</p>
            </ExplanatorySection>
          </div>

          <div className="home-feature">
            <Link className="feature-card feature-comparison" href="/official-leaderboard-comparison">
              <div>
                <p>OFFICIAL LEADERBOARDS</p>
                <h2>Player Comparison</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About Player Comparison">
              <p>Compare two players' estimated BPM skills and official scores across every S20+ and D20+ chart.</p>
            </ExplanatorySection>
          </div>
        </div>

        <ExplanatorySection className="home-how-to" title="Sync your scores before you start">
          <div className="home-how-to-copy">
            <ol>
              <li>Log in to PIU Scores and make your account public so your scores can pass through the API.</li>
              <li>Open the Phoenix score upload page and sync your latest scores.</li>
              <li>Return here to view recommendations based on the updated history.</li>
            </ol>
            <ScoreSyncLink>Open PIU Scores upload</ScoreSyncLink>
          </div>
        </ExplanatorySection>
      </section>

      <footer className="home-footer">
        <ExplanatorySection title="About the data">
          <p>Built from consented score data. Top 50 display scores are public; player IDs and complete score histories stay private.</p>
          <p>Phoenix 2 is authoritative for every recommendation chart and overlapping score.</p>
        </ExplanatorySection>
      </footer>
    </main>
  );
}
