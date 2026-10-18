import Link from "next/link";
import { ExplanatorySection } from "./_components/explanatory-section";
import { ScoreSyncLink } from "./_components/score-sync-link";
import { SiteHeader } from "./_components/site-header";

export default function HomePage() {
  return (
    <main className="home-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <SiteHeader />

      <section className="home-hero">
        <div className="feature-grid">
          <div className="home-feature">
            <Link className="feature-card feature-recommendations" href="/recommendations">
              <div>
                <p>PERSONALIZED ROUTE</p>
                <h2>Recommendations</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About Recommendations">
              <p>
                Select a username, see which version supplies your skill rating,
                and rank charts against your current Phoenix 2 history.
              </p>
            </ExplanatorySection>
          </div>

          <div className="home-feature">
            <Link className="feature-card feature-tier" href="/tier-list">
              <div>
                <p>GLOBAL ANALYSIS</p>
                <h2>Tier List</h2>
              </div>
              <b aria-hidden="true">↗</b>
            </Link>
            <ExplanatorySection title="About the Tier List">
              <p>
                Compare Scoring, Clearing, and Pumbility difficulty estimates
                in compact or detailed views, using Phoenix 1 and Phoenix 2 scores.
              </p>
            </ExplanatorySection>
          </div>
          <div className="home-feature">
            <Link className="feature-card feature-radar" href="/skill-radar"><div><p>YOUR BPM PROFILE</p><h2>Skill Radar</h2></div><b aria-hidden="true">↗</b></Link>
            <ExplanatorySection title="About Skill Radar"><p>Compare your Singles and Doubles across BPM ranges and find your next charts.</p></ExplanatorySection>
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
