import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import { PlayerProfileProvider } from "./_components/player-profile-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pumbility Farmer",
  description: "Player-normalized PIU scoring difficulty rankings for Singles and Doubles.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <PlayerProfileProvider>{children}</PlayerProfileProvider>
        <Analytics />
      </body>
    </html>
  );
}
