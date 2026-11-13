import { useState, useEffect, useCallback } from "react";
import { HeroGrid } from "./HeroGrid";
import type {
  Hero,
  RecommendationResponse,
  RecommendationRequest,
} from "../types/draft.ts";
import { API_BASE, fetchBanRecommendations, fetchPickRecommendations, isRetryableDraftError, prewarmBackend, shouldPrewarmBackend } from "../api.ts";
import { draftOrder } from "../data/draftOrder";
import { heroes } from "../data/heroes";
import { TIME_PER_ACTION, SLOT_COUNT } from "../constants/draft.ts";
import { BanSlotsRow } from "./BanSlotsRow.tsx";
import { DraftHeader } from "./DraftHeader.tsx";
import { DraftControls } from "./DraftControls.tsx";
import { RecommendationBox } from "./RecommendationBox.tsx";
import { PickSlotsColumn } from "./PickSlotsColumn.tsx";

export function DraftInterface() {
  const [timeRemaining, setTimeRemaining] = useState(TIME_PER_ACTION);

  const [blueBans, setBlueBans] = useState<Hero[]>([]);
  const [redBans, setRedBans] = useState<Hero[]>([]);
  const [bluePicks, setBluePicks] = useState<Hero[]>([]);
  const [redPicks, setRedPicks] = useState<Hero[]>([]);
  const [stepSelectionsCount, setStepSelectionsCount] = useState(0);
  const [hasDraftStarted, setHasDraftStarted] = useState(false);
  const [recommendationResult, setRecommendationResult] = useState<{
    key: string;
    data?: RecommendationResponse;
    error?: string;
    canRetry?: boolean;
  } | null>(null);
  const [backendState, setBackendState] = useState<"unchecked" | "connecting" | "reachable" | "ready" | "unavailable">(
    shouldPrewarmBackend() ? "connecting" : "unchecked",
  );
  const [retryNonce, setRetryNonce] = useState(0);

  const [bannedHeroIds, setBannedHeroIds] = useState<
    Set<number>
  >(new Set());
  const [pickedHeroIds, setPickedHeroIds] = useState<
    Set<number>
  >(new Set());

  const [currentDraftIndex, setCurrentDraftIndex] = useState(0);
  const currentStep =
    currentDraftIndex < draftOrder.length
      ? draftOrder[currentDraftIndex]
      : null;
  const currentTeam = currentStep ? currentStep.team : null;
  const currentAction = currentStep ? currentStep.action : "complete";
  const requestKey = JSON.stringify([
    hasDraftStarted, currentDraftIndex, bluePicks, redPicks, blueBans, redBans,
  ]);
  const activeResult = recommendationResult?.key === requestKey ? recommendationResult : null;
  const recommendations = activeResult?.data?.recommendations ?? [];
  const recommendationError = activeResult?.error ?? null;
  const recommendationStatus = activeResult?.data ? "ready" :
    activeResult?.error ? "unavailable" :
      backendState === "reachable" || backendState === "ready" ? "processing" : "connecting";

  useEffect(() => {
    if (!shouldPrewarmBackend()) return;
    const controller = new AbortController();
    prewarmBackend(controller.signal).then(
      () => setBackendState((current) => current === "connecting" ? "reachable" : current),
      () => {
        if (!controller.signal.aborted) {
          setBackendState((current) => current === "connecting" ? "unavailable" : current);
        }
      },
    );
    return () => controller.abort();
  }, []);

  const getRandomAvailableHero = useCallback(() => {
  const availableHeroes = heroes.filter(
    (hero) => !bannedHeroIds.has(hero.id) && !pickedHeroIds.has(hero.id)
  );
  if (availableHeroes.length === 0) return null;
  return availableHeroes[Math.floor(Math.random() * availableHeroes.length)];
}, [bannedHeroIds, pickedHeroIds]);


const applyHeroSelection = useCallback((hero: Hero, currentIndex: number) => {
  const current = draftOrder[currentIndex];
  if (!current) return;

  let selectionApplied = false;

  if (current.action === "ban") {
    if (current.team === "blue" && blueBans.length < SLOT_COUNT) {
      setBlueBans((prev) => [...prev, hero]);
      setBannedHeroIds((prev) => new Set([...prev, hero.id]));
      selectionApplied = true;
    } else if (current.team === "red" && redBans.length < SLOT_COUNT) {
      setRedBans((prev) => [...prev, hero]);
      setBannedHeroIds((prev) => new Set([...prev, hero.id]));
      selectionApplied = true;
    }
  } else if (current.action === "pick") {
    if (current.team === "blue" && bluePicks.length < SLOT_COUNT) {
      setBluePicks((prev) => [...prev, hero]);
      setPickedHeroIds((prev) => new Set([...prev, hero.id]));
      selectionApplied = true;
    } else if (current.team === "red" && redPicks.length < SLOT_COUNT) {
      setRedPicks((prev) => [...prev, hero]);
      setPickedHeroIds((prev) => new Set([...prev, hero.id]));
      selectionApplied = true;
    }
  }

  if (!selectionApplied) return;
  setTimeRemaining(TIME_PER_ACTION);

  const nextSelectionsMade = stepSelectionsCount + 1;
  const isStepComplete = nextSelectionsMade >= current.count;

  if (isStepComplete) {
    setCurrentDraftIndex((i) =>
      i + 1 >= draftOrder.length ? draftOrder.length : i + 1
    );
    setStepSelectionsCount(0);
    setTimeRemaining(TIME_PER_ACTION);
  } else {
    setStepSelectionsCount(nextSelectionsMade);
  }
}, [blueBans.length, redBans.length, bluePicks.length, redPicks.length, stepSelectionsCount]);

  const handleHeroSelect = (hero: Hero) => {
    if (!hasDraftStarted) return;
    if (currentDraftIndex >= draftOrder.length) return;
    if (
      bannedHeroIds.has(hero.id) ||
      pickedHeroIds.has(hero.id)
    ) {
      return;
    }

    applyHeroSelection(hero, currentDraftIndex);
  };

const handleResetDraft = () => {
  setBlueBans([]);
  setRedBans([]);
  setBluePicks([]);
  setRedPicks([]);

  setBannedHeroIds(new Set());
  setPickedHeroIds(new Set());
  setCurrentDraftIndex(0);
  setStepSelectionsCount(0);
  setTimeRemaining(TIME_PER_ACTION);
  setHasDraftStarted(false);
  setRecommendationResult(null);
  setRetryNonce(0);
}

const handleRetryRecommendations = () => {
  setRecommendationResult(null);
  setBackendState("connecting");
  setRetryNonce((value) => value + 1);
};

  const blueBanActiveIndex =
    currentAction === "ban" && currentTeam === "blue" ? blueBans.length : -1;

  const redBanActiveIndex =
    currentAction === "ban" && currentTeam === "red" ? redBans.length : -1;

useEffect(() => {
  if(!hasDraftStarted) return;
  if (currentDraftIndex >= draftOrder.length) return;

  const timer = setInterval(() => {
    setTimeRemaining((prev) => Math.max(prev - 1, 0));
  }, 1000);

  return () => clearInterval(timer);
}, [currentDraftIndex, hasDraftStarted]);

useEffect(() => {
  if (!hasDraftStarted || timeRemaining !== 0) return;
  if (currentDraftIndex >= draftOrder.length) return;

  const id = setTimeout(() => {
    const randomHero = getRandomAvailableHero();

    if (randomHero) {
      applyHeroSelection(randomHero, currentDraftIndex);
    } else {
      setCurrentDraftIndex((i) =>
        i + 1 >= draftOrder.length ? draftOrder.length : i + 1
      );
      setStepSelectionsCount(0);
      setTimeRemaining(TIME_PER_ACTION);
    }
  }, 0);

  return () => clearTimeout(id);
}, [timeRemaining, currentDraftIndex, hasDraftStarted, getRandomAvailableHero, applyHeroSelection]);

useEffect(() => {
  if (!hasDraftStarted) return;
  if (!currentStep) return;

  const payload: RecommendationRequest = {
    team: currentStep.team,
    blue_picks: bluePicks.map((h) => h.name),
    red_picks: redPicks.map((h) => h.name),
    blue_bans: blueBans.map((h) => h.name),
    red_bans: redBans.map((h) => h.name),
    top_k: 3,
    strict_turn: true,
    rerank_pool_size: null,
  };

  const controller = new AbortController();

  async function fetchData() {
    try {
      if (!currentStep) return;
      const requestAdvice =
        currentStep.action === "ban"
          ? fetchBanRecommendations
          : fetchPickRecommendations;
      const data = await requestAdvice(payload, controller.signal, () => {
        if (!controller.signal.aborted) setBackendState("connecting");
      });
      if (controller.signal.aborted) return;
      setBackendState("ready");
      setRecommendationResult({ key: requestKey, data });
    } catch (err) {
      if (controller.signal.aborted) return;
      setBackendState("unavailable");
      setRecommendationResult({
        key: requestKey,
        error: `Recommendations unavailable from ${API_BASE}. ${err instanceof Error ? err.message : "Request failed."}`,
        canRetry: isRetryableDraftError(err),
      });
    }
  }

  fetchData();

  return () => controller.abort();
}, [bluePicks, redPicks, blueBans, redBans, currentStep, hasDraftStarted, requestKey, retryNonce]);

  return (
    <div className="h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900 text-white flex flex-col overflow-hidden">
      <div className="w-full max-w-[1900px] mx-auto px-6 pt-3">
        <div className="bg-black/50 backdrop-blur-sm border border-white/10 rounded-xl px-4 py-2 flex items-center justify-between">
          <div className="w-[360px] flex justify-start">
            <BanSlotsRow
            heroes = {blueBans}
            activeIndex={blueBanActiveIndex}
            side="blue"
            align="left"
            />
          </div>

          <DraftHeader
            currentAction={currentAction}
            currentTeam={currentTeam}
            timeRemaining={timeRemaining}
            hasDraftStarted={hasDraftStarted}
            phase={currentStep?.phase}
            batch={currentStep?.batch}
            remaining={currentStep ? currentStep.count - stepSelectionsCount : 0}
          />

          <div className="w-[360px] flex justify-end items-center gap-2">
            <DraftControls
              hasDraftStarted={hasDraftStarted}
              onStart={() => setHasDraftStarted(true)}
              onReset={handleResetDraft}
            />

            <BanSlotsRow
            heroes = {redBans}
            activeIndex={redBanActiveIndex}
            side="red"
            align="right"
            />
          </div>
        </div>
      </div>

  <div className="flex-1 w-full max-w-[1900px] mx-auto px-6 py-4 grid grid-cols-[140px_minmax(180px,220px)_minmax(0,1fr)_minmax(180px,220px)_140px] gap-4 min-h-0 overflow-hidden">
    {/* BLUE PICKS */}
    <div className="min-h-0">
      <PickSlotsColumn
        heroes={bluePicks}
        currentAction={currentAction}
        currentTeam={currentTeam}
        side="blue"
      />
    </div>

    {/* BLUE RECOMMENDATIONS */}
    <div className="min-h-0">
      <RecommendationBox
        team="blue"
        recommendations={recommendations}
        visible={hasDraftStarted && currentTeam === "blue"}
        status={recommendationStatus}
        error={recommendationError}
        canRetry={activeResult?.canRetry ?? false}
        onRetry={handleRetryRecommendations}
        limitation={activeResult?.data?.training_context?.limitation}
        isDoublePick={currentStep?.count === 2}
      />
    </div>

    {/* CENTER */}
    <div className="min-w-0 min-h-0 overflow-y-auto custom-scrollbar">
      <HeroGrid
        heroes={heroes}
        onHeroSelect={handleHeroSelect}
        bannedHeroIds={bannedHeroIds}
        pickedHeroIds={pickedHeroIds}
        currentAction={currentAction}
      />
    </div>

    {/* RED RECOMMENDATIONS */}
    <div className="min-h-0">
      <RecommendationBox
        team="red"
        recommendations={recommendations}
        visible={hasDraftStarted && currentTeam === "red"}
        status={recommendationStatus}
        error={recommendationError}
        canRetry={activeResult?.canRetry ?? false}
        onRetry={handleRetryRecommendations}
        limitation={activeResult?.data?.training_context?.limitation}
        isDoublePick={currentStep?.count === 2}
      />
    </div>

    {/* RED PICKS */}
    <div className="min-h-0">
      <PickSlotsColumn
        heroes={redPicks}
        currentAction={currentAction}
        currentTeam={currentTeam}
        side="red"
      />
    </div>
  </div>
</div>
  );
}
