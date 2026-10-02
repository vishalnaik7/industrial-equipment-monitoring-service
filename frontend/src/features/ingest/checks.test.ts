import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { checkBatch, jsonErrorWhere } from "./checks";

const sample = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../../../backend/sample_payloads/${name}.json`, import.meta.url), "utf8"));
const codes = (r: ReturnType<typeof checkBatch>) => r.items.map((i) => i.code).filter(Boolean);

describe("checkBatch", () => {
  it("accepts a clean vendor sample", () => {
    const r = checkBatch(JSON.stringify(sample("pulseforge_sample")), "pulseforge", 100);
    expect(r.ok).toBe(true);
    expect(r.recordCount).toBe(2);
    expect(r.items.filter((i) => i.level === "bad" || i.level === "warn")).toEqual([]);
  });

  it("blocks invalid JSON", () => {
    const r = checkBatch('{\n  "events": [1,}', "pulseforge", 100);
    expect(r.ok).toBe(false);
    expect(codes(r)).toEqual(["INVALID_JSON"]);
    expect(r.items[0].text).toMatch(/^Invalid JSON - Unexpected token/);
  });

  it("maps engine-specific JSON error positions to line and column", () => {
    const text = '{\n  "a": 1,\n  oops\n}';
    expect(jsonErrorWhere(new Error(`Unexpected token 'o', "..." is not valid JSON`), text)).toBe("Unexpected token 'o'");
    expect(jsonErrorWhere(new Error("Unexpected token o in JSON at position 14"), text)).toBe("Unexpected token o at line 3, column 3");
    expect(jsonErrorWhere(new Error("JSON.parse: expected property name at line 3 column 3 of the JSON data"), text))
      .toBe("expected property name at line 3, column 3");
  });

  it.each([
    ["[1]", "NOT_OBJECT"],
    ['{"plant_id": "PLANT_01"}', "MISSING_RECORDS"],
    ['{"events": "x"}', "RECORDS_NOT_LIST"],
    ['{"events": []}', "EMPTY_BATCH"],
    ['{"events": [{"event_id": "a", "machine_id": "EQ-1", "event_time": "t"}, 5]}', "RECORD_NOT_OBJECT"],
  ])("blocks malformed envelope %s", (body, code) => {
    const r = checkBatch(body, "pulseforge", 100);
    expect(r.ok).toBe(false);
    expect(codes(r)).toContain(code);
  });

  it("detects a body sent to the wrong vendor and suggests the right one", () => {
    const tw = sample("thermexwatch_sample");
    const r = checkBatch(JSON.stringify(tw), "pulseforge", 100);
    expect(r.ok).toBe(false);
    expect(r.suggest).toBe("thermexwatch");
    const mismatch = checkBatch(JSON.stringify({ ...tw, events: tw.readings, vendor: "ThermexWatch" }), "pulseforge", 100);
    expect(codes(mismatch)).toContain("VENDOR_MISMATCH");
  });

  it("warns (does not block) on records the worker will reject", () => {
    const r = checkBatch(JSON.stringify(sample("malformed").thermexwatch), "thermexwatch", 100);
    expect(r.ok).toBe(true);
    expect(codes(r)).toContain("MISSING_REQUIRED");
  });

  it("flags repeated ids and the per-batch limit", () => {
    expect(codes(checkBatch(JSON.stringify(sample("duplicates").pulseforge), "pulseforge", 100))).toContain("DUPLICATE_IDS");
    const big = checkBatch(JSON.stringify({ events: [{}, {}, {}] }), "pulseforge", 2);
    expect(big.ok).toBe(false);
    expect(codes(big)).toContain("BATCH_TOO_LARGE");
  });

  it("is idle for empty input", () => {
    expect(checkBatch("   ", "maintaflow", 10)).toEqual({ ok: false, items: [{ level: "idle", text: expect.any(String) }] });
  });
});

describe("work orders (CMMS)", () => {
  const wo = { work_order_id: "WO-1002", machine_id: "EQ-004", opened_at: "2026-04-18T07:35:00Z", status: "closed" };
  it("accepts a CMMS envelope", () => {
    expect(checkBatch(JSON.stringify({ source: "CMMS", plant_id: "PLANT_01", work_orders: [wo] }), "workorders", 10).ok).toBe(true);
  });
  it("suggests the work-orders endpoint for a CMMS body sent elsewhere", () => {
    const r = checkBatch(JSON.stringify({ source: "CMMS", work_orders: [wo] }), "thermexwatch", 10);
    expect(r.ok).toBe(false);
    expect(r.suggest).toBe("workorders");
  });
});
