import type { Recommendation } from "../types/draft";
import { getHeroImage } from "../../../shared/utils/HeroImage";

type RecommendationBoxProps = {
  team: "blue" | "red";
  recommendations: Recommendation[];
  visible: boolean;
  isLoading: boolean;
  error: string | null;
  limitation?: string | null;
  isDoublePick?: boolean;
};

export function RecommendationBox({
  team,
  recommendations,
  visible,
  isLoading,
  error,
  limitation,
  isDoublePick,
}: RecommendationBoxProps) {
  if (!visible) return null;

  return (
    <div className="w-full rounded-xl border border-white/10 bg-gradient-to-b from-white/5 to-transparent backdrop-blur-md p-3 text-white shadow-lg">
      <div className="recommendation-box__header">
        {team === "blue" ? "Blue Suggestions" : "Red Suggestions"}
      </div>

      {isDoublePick && <p className="text-xs text-gray-300 my-2">Choose two heroes in either order. Suggestions update after each choice.</p>}
      {limitation && <p className="text-xs text-amber-200 my-2">{limitation}</p>}
      {error ? (
        <div className="text-sm text-red-300" role="alert">
          {error}
        </div>
      ) : isLoading ? (
        <div className="text-sm text-gray-300" role="status">Loading recommendations...</div>
      ) : recommendations.length === 0 ? (
        <div className="text-sm text-gray-300">No recommendations returned for this draft state.</div>
      ) : (
        <div className="flex-1 overflow-y-auto flex flex-col gap-2 pr-1">
          {recommendations.map((rec) => (
            <div
              key={rec.hero}
              className="rounded-lg bg-white/5 px-3 py-2 flex items-start justify-between gap-3"
            >
              <div className="min-w-0">
                <div className="font-medium text-sm">
                  #{rec.rank} {rec.hero}
                  <img
                    src={getHeroImage(rec.hero)}
                    alt={rec.hero}
                    className="w-10 h-10 rounded-full object-cover border border-white/10"
                  />
                </div>

                {rec.reasons[0] && (
                  <div className="text-xs text-gray-400 mt-1 line-clamp-2">
                    {rec.reasons[0]}
                  </div>
                )}
              </div>

              <div className="shrink-0 text-xs text-gray-300">
                {rec.score.toFixed(2)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
