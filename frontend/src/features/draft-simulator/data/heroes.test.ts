/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { heroes } from "./heroes";

describe("hero catalog", () => {
  it("keeps canonical hero IDs and names unique", () => {
    expect(new Set(heroes.map((hero) => hero.id)).size).toBe(heroes.length);
    expect(new Set(heroes.map((hero) => hero.name)).size).toBe(heroes.length);
    expect(heroes.find((hero) => hero.id === 133)).toEqual({
      id: 133,
      name: "Hirara",
      role: ["Assassin"],
      image: `${import.meta.env.BASE_URL}HeroIcon/Hirara.png`,
    });
  });

  it("ships Hirara's actual PNG icon at the catalog path", () => {
    const icon = readFileSync(resolve("public/HeroIcon/Hirara.png"));
    expect([...icon.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
    expect(icon.readUInt32BE(16)).toBe(128);
    expect(icon.readUInt32BE(20)).toBe(128);
  });
});
