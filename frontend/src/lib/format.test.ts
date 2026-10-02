import { describe, expect, it } from "vitest";
import { humanAge, relTime, vendorLabel, vendorSlug } from "./format";

describe("format", () => {
  it("maps canonical source names to API slugs (trace links)", () => {
    expect(vendorSlug("PulseForge")).toBe("pulseforge");
    expect(vendorSlug("CMMS")).toBe("workorders");
    expect(vendorLabel("workorders")).toBe("Work orders");
  });
  it("formats relative times and ages", () => {
    const now = Date.parse("2026-04-18T09:00:00Z");
    expect(relTime("2026-04-18T08:59:30Z", now)).toBe("30s ago");
    expect(relTime("2026-04-18T07:00:00Z", now)).toBe("2h ago");
    expect(relTime(null)).toBe("-");
    expect(humanAge(90061)).toBe("1d 1h");
  });
});
