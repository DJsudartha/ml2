export type Role =
  | "Tank"
  | "Fighter"
  | "Assassin"
  | "Mage"
  | "Marksman"
  | "Support"
  | "Other";

export type DraftPhase = "ban1" | "pick1" | "ban2" | "pick2";

export type DraftAction = "ban" | "pick";

export type Team = "blue" | "red";

export interface Hero {
  id: number;
  name: string;
  role: Role[];     
  image: string;
}

export interface DraftStep {
  phase: DraftPhase;
  team: Team;
  action: DraftAction;
}

export interface RecommendationRequest {
  team: "blue" | "red";
  blue_picks: string[];
  red_picks: string[];
  blue_bans: string[];
  red_bans: string[];
  top_k?: number;
  strict_turn?: boolean;
  rerank_pool_size?: number | null;
}

export interface Recommendation {
  hero: string;
  rank: number;
  score: number;
  reasons: string[];
  score_components?: Record<string, number>;
}

export interface RecommendationResponse {
  team: Team;
  recommendations: Recommendation[];
  reasoning?: string;
  training_context?: PickTrainingContext;
}

export interface PickTrainingContext {
  target: "order-agnostic-pick-fit" | "confirmed-ordered-pick";
  uses_confirmed_pick_order: boolean;
  limitation: string | null;
}

export interface AdvisorResponse {
  uses_llm: boolean;
  provider: string;
  model: string;
  advice: string;
  retrieved_principles: unknown[];
}

export interface AdviceResponse {
  recommendation: RecommendationResponse;
  advisor: AdvisorResponse;
}
