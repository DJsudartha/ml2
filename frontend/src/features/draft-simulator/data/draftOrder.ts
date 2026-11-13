import type { DraftStep } from "../types/draft";

export const draftOrder: readonly DraftStep[] = [
    // First ban phase (3 bans each)
    { phase: "ban1", team: "blue", action: "ban" , count: 1},
    { phase: "ban1", team: "red", action: "ban" , count: 1},
    { phase: "ban1", team: "blue", action: "ban" , count: 1},
    { phase: "ban1", team: "red", action: "ban" , count: 1},
    { phase: "ban1", team: "blue", action: "ban" , count: 1},
    { phase: "ban1", team: "red", action: "ban" , count: 1},

    // First pick phase
    { phase: "pick1", team: "blue", action: "pick", count: 1, batch: "B1" },
    { phase: "pick1", team: "red", action: "pick", count: 2, batch: "R1/R2" },
    { phase: "pick1", team: "blue", action: "pick", count: 2, batch: "B2/B3" },
    { phase: "pick1", team: "red", action: "pick", count: 1, batch: "R3" },

    // Second ban phase
    { phase: "ban2", team: "red", action: "ban", count: 1},
    { phase: "ban2", team: "blue", action: "ban", count: 1},
    { phase: "ban2", team: "red", action: "ban", count: 1},
    { phase: "ban2", team: "blue", action: "ban", count: 1},

    // Final pick phase
    { phase: "pick2", team: "red", action: "pick", count: 1, batch: "R4" },
    { phase: "pick2", team: "blue", action: "pick", count: 2, batch: "B4/B5" },
    { phase: "pick2", team: "red", action: "pick", count: 1, batch: "R5" },
  ];