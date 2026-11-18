import { DEFAULT_LOCALE, isLocale, type Locale } from "@careerpilot/i18n";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Non-sensitive UI preferences only. Session tokens must NEVER be stored here
 * (localStorage is readable by any script in the page) — see docs/SECURITY.md.
 */
interface PreferencesState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const usePreferences = create<PreferencesState>()(
  persist(
    (set) => ({
      locale: DEFAULT_LOCALE,
      setLocale: (locale) => set({ locale }),
    }),
    {
      name: "careerpilot.preferences",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      // Hydrated explicitly after mount so server and first client render match (Next.js).
      skipHydration: true,
      merge: (persisted, current) => {
        const locale = (persisted as Partial<PreferencesState> | undefined)?.locale;
        return { ...current, locale: isLocale(locale) ? locale : current.locale };
      },
    },
  ),
);
