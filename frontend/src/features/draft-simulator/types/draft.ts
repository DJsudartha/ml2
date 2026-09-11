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
  team: Team;
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

export interface PickOrderProfile {
  id: string;
  title: string;
  summary: string;
  base_score_weight: number;
  secure_power_weight: number;
  flexibility_weight: number;
  synergy_weight: number;
  counter_weight: number;
  role_completion_weight: number;
  redundancy_penalty_weight: number;
}

export interface RecommendationResponse {
  team: Team;
  recommendations: Recommendation[];
  phase_index: number;
  rerank_pool_size?: number;
  ban_order?: number;
  pick_order?: number;
  global_pick_index?: number;
  order_profile?: PickOrderProfile;
  base_model_source?: string;
  base_model_name?: string;
}

export interface RetrievedPrinciple {
  id: string;
  title: string;
  text: string;
  score: number;
}

export interface AdvisorResponse {
  uses_llm: boolean;
  provider: string;
  model: string | null;
  advice: string;
  retrieved_principles: RetrievedPrinciple[];
  error: string | null;
}

export interface DraftAdviceResponse {
  recommendation: RecommendationResponse;
  advisor: AdvisorResponse;
}
