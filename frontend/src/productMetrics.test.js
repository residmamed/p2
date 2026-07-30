import { describe, expect, it } from "vitest";
import {
  marketSnapshot,
  opportunityScores,
  parsePrice,
  parseVolumeOz,
  pricePerOz,
  ratingConfidenceScore,
} from "./productMetrics";

const priced = (price_text, rest = {}) => ({ title: "x", price_text, ...rest });

describe("parsePrice", () => {
  it("reads the first number out of a price string", () => {
    expect(parsePrice("$5.98")).toBe(5.98);
    expect(parsePrice("$1.20 - $1.80")).toBe(1.2);
  });

  it("converts yuan at the documented rate rather than reporting it as dollars", () => {
    expect(parsePrice("¥6.5 - ¥9.0")).toBeCloseTo(6.5 / 7.2, 5);
  });

  it("strips thousands separators instead of truncating at the comma", () => {
    expect(parsePrice("$1,299.00")).toBe(1299);
  });

  it("returns null rather than NaN when there is no number", () => {
    expect(parsePrice(null)).toBeNull();
    expect(parsePrice("")).toBeNull();
    expect(parsePrice("Price on request")).toBeNull();
  });
});

describe("ratingConfidenceScore", () => {
  it("marks down a perfect rating that almost nobody left", () => {
    const unproven = ratingConfidenceScore({ rating: 5.0, review_count: 2 });
    const proven = ratingConfidenceScore({ rating: 4.7, review_count: 21000 });
    expect(unproven).toBeLessThan(proven);
  });

  it("keeps a well-evidenced rating close to its face value", () => {
    const score = ratingConfidenceScore({ rating: 4.7, review_count: 21000 });
    expect(score).toBeGreaterThan(4.6);
    expect(score).toBeLessThanOrEqual(4.7);
  });

  it("rises monotonically with review count at a fixed rating", () => {
    const counts = [10, 100, 1000, 10000];
    const scores = counts.map((n) => ratingConfidenceScore({ rating: 4.5, review_count: n }));
    for (let i = 1; i < scores.length; i += 1) {
      expect(scores[i]).toBeGreaterThan(scores[i - 1]);
    }
  });

  it("floors a rating published with no review count, rather than trusting it", () => {
    expect(ratingConfidenceScore({ rating: 5.0, review_count: null })).toBe(0);
  });

  it("returns null when there is no rating at all", () => {
    expect(ratingConfidenceScore({ rating: null, review_count: 500 })).toBeNull();
  });
});

describe("opportunityScores", () => {
  const set = [
    priced("$19.99", { rating: 4.6, review_count: 13835 }),
    priced("$45.00", { rating: 4.7, review_count: 203137 }),
    priced("$16.99", { rating: 4.4, review_count: 500 }),
  ];

  it("returns one entry per product, aligned by index", () => {
    expect(opportunityScores(set)).toHaveLength(set.length);
  });

  it("scores every listing between 0 and 100", () => {
    for (const m of opportunityScores(set)) {
      expect(m.score).toBeGreaterThanOrEqual(0);
      expect(m.score).toBeLessThanOrEqual(100);
    }
  });

  it("leaves a listing with no rating and no reviews unscoreable, not mid-scale", () => {
    const withPin = [...set, priced("$10.00")];
    const scores = opportunityScores(withPin);
    expect(scores[3]).toBeNull();
  });

  it("still scores a listing that is missing only some inputs", () => {
    // Rated and reviewed but unpriced: the store didn't publish a price, which
    // is not the same as being bad value.
    const scores = opportunityScores([...set, priced(null, { rating: 4.8, review_count: 900 })]);
    expect(scores[3]).not.toBeNull();
    expect(scores[3].basis).not.toContain("value");
    expect(scores[3].basis).toContain("quality");
  });

  it("ranks demand on a log scale so one huge listing does not flatten the rest", () => {
    const scores = opportunityScores(set);
    // 13,835 reviews sits between 500 and 203,137. Linear normalization would
    // put it at ~0.07; log scaling should place it near the middle.
    expect(scores[0].demand).toBeGreaterThan(0.3);
    expect(scores[0].demand).toBeLessThan(0.9);
  });

  it("reports price against the median with the sign pointing the documented way", () => {
    const scores = opportunityScores(set);
    // Median of 19.99 / 45.00 / 16.99 is 19.99, so the first listing is at it.
    expect(scores[0].priceVsMedianPct).toBeCloseTo(0, 5);
    expect(scores[1].priceVsMedianPct).toBeGreaterThan(0); // dearer than median
    expect(scores[2].priceVsMedianPct).toBeLessThan(0); // cheaper than median
  });

  it("gives a cheaper listing a higher value component than a dearer one", () => {
    const scores = opportunityScores(set);
    expect(scores[2].value).toBeGreaterThan(scores[1].value);
  });

  it("does not divide by zero when every listing shares a value", () => {
    const flat = [
      priced("$10.00", { rating: 4.5, review_count: 100 }),
      priced("$10.00", { rating: 4.5, review_count: 100 }),
    ];
    const scores = opportunityScores(flat);
    for (const m of scores) {
      expect(Number.isFinite(m.score)).toBe(true);
      expect(m.demand).toBe(0.5);
      expect(m.value).toBe(0.5);
    }
  });

  it("handles an empty set without throwing", () => {
    expect(opportunityScores([])).toEqual([]);
    expect(opportunityScores(null)).toEqual([]);
  });

  it("scores a zero-review listing rather than treating it as unscoreable", () => {
    // review_count 0 is a measurement ("nobody has reviewed it"); null is an
    // absence ("we were never told"). Only the latter is unscoreable.
    const scores = opportunityScores([
      priced("$10.00", { rating: 4.0, review_count: 0 }),
      priced("$20.00", { rating: 4.5, review_count: 900 }),
    ]);
    expect(scores[0]).not.toBeNull();
    expect(scores[0].demand).toBe(0);
  });
});

describe("marketSnapshot", () => {
  it("summarises an empty set without producing NaN", () => {
    const s = marketSnapshot([]);
    expect(s.count).toBe(0);
    expect(s.medianPrice).toBeNull();
    expect(s.avgRating).toBeNull();
    expect(s.totalReviews).toBe(0);
  });

  it("takes the median of an even-sized set as the mean of the middle two", () => {
    const s = marketSnapshot([
      priced("$10.00"),
      priced("$20.00"),
      priced("$30.00"),
      priced("$40.00"),
    ]);
    expect(s.medianPrice).toBe(25);
  });

  it("reads competition off review depth, not result count", () => {
    const shallow = marketSnapshot([
      priced("$10", { rating: 4, review_count: 10 }),
      priced("$12", { rating: 4, review_count: 20 }),
    ]);
    const deep = marketSnapshot([
      priced("$10", { rating: 4, review_count: 9000 }),
      priced("$12", { rating: 4, review_count: 11000 }),
    ]);
    expect(shallow.competition).toBe("low");
    expect(deep.competition).toBe("high");
  });

  it("counts every listing in the site mix", () => {
    const s = marketSnapshot([
      { title: "a", site: "amazon", price_text: "$1" },
      { title: "b", site: "amazon", price_text: "$2" },
      { title: "c", site: "walmart", price_text: "$3" },
    ]);
    expect(s.siteMix[0]).toMatchObject({ site: "amazon", count: 2 });
    expect(s.siteMix.reduce((n, m) => n + m.count, 0)).toBe(3);
  });
});

describe("parseVolumeOz / pricePerOz", () => {
  it("reads a plain ounce size out of a title", () => {
    expect(parseVolumeOz("Owala FreeSip Water Bottle 24 oz Very Dark")).toBe(24);
    expect(parseVolumeOz("STANLEY Quencher H2.0 Tumbler 30 oz")).toBe(30);
  });

  it("handles the 'fl oz' spelling", () => {
    expect(parseVolumeOz("Hydro Flask 32 fl oz Wide Mouth")).toBe(32);
  });

  it("converts metric to fluid ounces", () => {
    expect(parseVolumeOz("Coffee Carafe 1.5 Liter Thermal")).toBeCloseTo(50.721, 2);
    expect(parseVolumeOz("Travel Mug 500ml")).toBeCloseTo(16.907, 2);
  });

  it("multiplies by the pack count, so a 4-pack is not priced as one item", () => {
    expect(parseVolumeOz("Ribbed Glass Tumbler 4Pcs - 20oz Leak Proof")).toBe(80);
    expect(parseVolumeOz("Set of 16 Drinking Glasses 12 oz")).toBe(192);
    expect(parseVolumeOz("Mason Jars 2-Pack 16 oz")).toBe(32);
  });

  it("returns null rather than guessing when the title states no size", () => {
    expect(parseVolumeOz("Alpha Grillers Instant Read Meat Thermometer")).toBeNull();
    expect(parseVolumeOz("")).toBeNull();
    expect(parseVolumeOz(null)).toBeNull();
  });

  it("rejects an implausible single-vessel volume as a parse artefact", () => {
    // "2024" is a year, not a 2,024-ounce tumbler.
    expect(parseVolumeOz("Tumbler 2024 oz Limited Edition")).toBeNull();
  });

  it("does not treat weight as volume", () => {
    // Grams and pounds are mass; pooling them with fluid ounces would compare
    // an ounce of coffee to an ounce of water.
    expect(parseVolumeOz("Coffee Beans 340 g Medium Roast")).toBeNull();
    expect(parseVolumeOz("Protein Powder 2 lb Vanilla")).toBeNull();
  });

  it("prices per ounce only when both halves are known", () => {
    expect(pricePerOz({ title: "Tumbler 20 oz", price_text: "$10.00" })).toBeCloseTo(0.5, 5);
    expect(pricePerOz({ title: "Tumbler", price_text: "$10.00" })).toBeNull();
    expect(pricePerOz({ title: "Tumbler 20 oz", price_text: null })).toBeNull();
  });

  it("makes a bigger bottle the better value at the same sticker price", () => {
    const big = pricePerOz({ title: "Bottle 40 oz", price_text: "$20.00" });
    const small = pricePerOz({ title: "Bottle 20 oz", price_text: "$20.00" });
    expect(big).toBeLessThan(small);
  });
});

describe("marketSnapshot per-oz", () => {
  it("reports the median unit price and how many listings it is based on", () => {
    const s = marketSnapshot([
      { title: "Bottle 20 oz", price_text: "$10.00" }, // 0.50
      { title: "Bottle 40 oz", price_text: "$20.00" }, // 0.50
      { title: "Thermometer", price_text: "$15.00" }, // no size
    ]);
    expect(s.perOzCount).toBe(2);
    expect(s.medianPerOz).toBeCloseTo(0.5, 5);
  });

  it("leaves the unit price absent when no listing states a size", () => {
    const s = marketSnapshot([{ title: "Thermometer", price_text: "$15.00" }]);
    expect(s.perOzCount).toBe(0);
    expect(s.medianPerOz).toBeNull();
  });
});
