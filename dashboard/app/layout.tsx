import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI API Reliability Gateway - Dashboard",
  description: "Live view of gateway circuit state, traffic, and rate limits",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
