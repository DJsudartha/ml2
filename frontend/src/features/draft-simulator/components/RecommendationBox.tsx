import type { Recommendation } from "../types/draft";
import { getHeroImage } from "../../../shared/utils/HeroImage";

type RecommendationBoxProps = {
  team: "blue" | "red";
  recommendations: Recommendation[];
  visible: boolean;
  status: "connecting" | "processing" | "ready" | "unavailable";
  error: string | null;
  canRetry: boolean;
  onRetry: () => void;
  limitation?: string | null;
  isDoublePick?: boolean;
};

export function RecommendationBox({
  team,
  recommendations,
  visible,
  status,
  error,
  canRetry,
  onRetry,
  limitation,
  isDoublePick,
}: RecommendationBoxProps) {
  if (!visible) return null;

  const statusLabel = status === "ready" ? "Ready" :
    status === "unavailable" ? "Unavailable" : "Connecting";

  return (
    <div className="w-full rounded-xl border border-white/10 bg-gradient-to-b from-white/5 to-transparent backdrop-blur-md p-3 text-white shadow-lg">
      <div className="recommendation-box__header">
        {team === "blue" ? "Blue Suggestions" : "Red Suggestions"}
      </div>
      <div className="mt-1 text-xs text-gray-300" aria-label="Recommendation status">
        Recommendations: {statusLabel}
      </div>

      {isDoublePick && <p className="text-xs text-gray-300 my-2">Choose two heroes in either order. Suggestions update after each choice.</p>}
      {limitation && <p className="text-xs text-amber-200 my-2">{limitation}</p>}
      {status === "unavailable" ? (
        <div className="text-sm text-red-300" role="alert">
          <p>{error}</p>
          {canRetry && <button type="button" onClick={onRetry} className="mt-2 rounded border border-red-300 px-2 py-1 text-white">
            Retry recommendations
          </button>}
          <p className="mt-2 text-xs text-gray-300">The 30-second draft timer continues. You can still select heroes manually.</p>
        </div>
      ) : status === "connecting" ? (
        <div className="text-sm text-gray-300" role="status">
          Connecting to backend. It may be waking up. The 30-second draft timer continues; you can select heroes manually.
        </div>
      ) : status === "processing" ? (
        <div className="text-sm text-gray-300" role="status">
          Backend reached. Processing recommendations. The 30-second draft timer continues.
        </div>
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
