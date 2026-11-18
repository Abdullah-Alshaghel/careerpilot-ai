import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "@careerpilot/ui/styles.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "CareerPilot AI",
  description: "AI-assisted career and job application workspace",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // lang/dir are updated on the client when the user switches locale.
    <html lang="en" dir="ltr" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
