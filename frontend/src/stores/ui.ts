import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

type UIState = {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (value: boolean) => void;
  toggleSidebar: () => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      setSidebarCollapsed: (value) => set({ sidebarCollapsed: value }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
    }),
    {
      name: "papergen-ui",
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
