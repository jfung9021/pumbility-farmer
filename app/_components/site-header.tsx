import Link from "next/link";

type SiteHeaderProps = {
  active?: "recommendations" | "tier-list" | "skill-radar" | "official-comparison";
};

export function SiteHeader({ active }: SiteHeaderProps) {
  return (
    <header className="site-header">
      <Link className="brand" href="/" aria-label="Pumbility Farmer home">
        <span className="brand-mark">PF</span>
        <span>
          Pumbility <b>Farmer</b>
        </span>
      </Link>

      {active ? (
        <nav className="page-nav" aria-label="Primary navigation">
          {active === "recommendations" ? (
            <span aria-current="page">Recommendations</span>
          ) : (
            <Link href="/recommendations">Recommendations</Link>
          )}
          {active === "tier-list" ? (
            <span aria-current="page">Tier List</span>
          ) : (
            <Link href="/tier-list">Tier List</Link>
          )}
          {active === "skill-radar" ? (
            <span aria-current="page">Skill Radar</span>
          ) : (
            <Link href="/skill-radar">Skill Radar</Link>
          )}
          {active === "official-comparison" ? (
            <span aria-current="page">Player Comparison</span>
          ) : (
            <Link href="/official-leaderboard-comparison">Player Comparison</Link>
          )}
        </nav>
      ) : null}
    </header>
  );
}
