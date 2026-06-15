import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "DAM Platform — Universal Search",
  description: "One search box for documents, images, audio & video.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
