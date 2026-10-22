"use client";

import Link from "next/link";
import { SiteHeader } from "./_components/site-header";
import { usePlayerProfile } from "./_components/player-profile-provider";
import styles from "./_components/player-shell.module.css";
import aboutStyles from "./about/about.module.css";

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
            <Link className={aboutStyles.contextLink} href="/about#recommendations">About Recommendations ↗</Link>
          </div>

          <div className="home-feature">
            <Link className="feature-card feature-tier" href={profileHref("/tier-list")}>
              <div>
                <p>GLOBAL ANALYSIS</p>
                <h2>Tier Lists</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <Link className={aboutStyles.contextLink} href="/about#tier-lists">About Tier Lists ↗</Link>
          </div>
          <div className="home-feature">
            <Link className="feature-card feature-radar" href={profileHref("/skill-radar")}>
              <div>
                <p>YOUR BPM PROFILE</p>
                <h2>Skill Radar</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <Link className={aboutStyles.contextLink} href="/about#skill-radar">About Skill Radar ↗</Link>
          </div>

          <div className="home-feature">
            <Link className="feature-card feature-comparison" href="/official-leaderboard-comparison">
              <div>
                <p>OFFICIAL LEADERBOARDS</p>
                <h2>Player Comparison</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <Link className={aboutStyles.contextLink} href="/about#player-comparison">About Player Comparison ↗</Link>
          </div>
        </div>

        <p className="home-how-to"><Link className={aboutStyles.contextLink} href="/about#score-sync">How to sync your scores ↗</Link></p>
      </section>

      <footer className="home-footer">
        <Link className={aboutStyles.contextLink} href="/about#data">Data sources and privacy ↗</Link>
      </footer>
    </main>
  );
}
