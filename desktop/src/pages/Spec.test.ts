import { describe, expect, it } from "vitest";
import { buildMatrixCsv } from "./Spec";
import type { ComplianceResult } from "../core/specApi";

const row = (id: string, cam: string, status: string, extra: object = {}) =>
  ({ profile_name: "P", requirement_id: id, requirement: "x", camera_model: cam, status, evidence: "", ...extra });

describe("buildMatrixCsv", () => {
  it("writes BOM, semicolons, CRLF, quoting and Turkish text", () => {
    const result = {
      requirements: [{ id: "R1", category: "Optik", requirement: "x" }],
      matrix: [
        row("R1", "Kamera A", "Uyumlu", { requirement: 'Yüz; "tanıma"\nşart', evidence_kind: "optik motor", standard_clause: "EN 62676-4 §6" }),
        row("R1", "Kamera B", "Kısmi", { user_status: "Uyumsuz" }),
      ],
    } as unknown as ComplianceResult;
    const csv = buildMatrixCsv(result);
    expect(csv.startsWith("﻿")).toBe(true);
    expect(csv.endsWith("\r\n")).toBe(true);
    expect(csv.slice(1)).toBe(
      "Madde;İster;Kategori;Kanıt türü;Standart maddesi;Kamera A;Kamera B\r\n" +
      'R1;"Yüz; ""tanıma""\nşart";Optik;optik motor;EN 62676-4 §6;Uyumlu;Uyumsuz\r\n',
    );
  });
});
