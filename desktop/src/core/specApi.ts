// Typed surface of the specification part of core API v1 (service/spec_methods.py).

import type { CallOptions, RpcClient } from "./rpc";

export type Status = "Uyumlu" | "Kısmi" | "Uyumsuz" | "Bulunamadı";

export interface Requirement {
  id: string;
  profile_id?: string;
  profile_name?: string;
  category: string;
  requirement: string;
  weight?: number;
  confidence?: number;
  spec_quote?: string;
  standard_clause?: string;
  required_ppm?: number;
  distance_m?: number;
  [k: string]: unknown;
}

export interface MatrixRow {
  profile_name: string;
  requirement_id: string;
  requirement: string;
  camera_model: string;
  status: Status;
  evidence: string;
  evidence_kind?: string;
  standard_clause?: string;
  confidence?: number;
  spec_quote?: string;
  user_status?: Status;
  user_note?: string;
  [k: string]: unknown;
}

export interface ComplianceResult {
  profiles: { id: string; name: string; description: string }[];
  requirements: Requirement[];
  matrix: MatrixRow[];
  camera_scores: { profile_name: string; camera_model: string; score: number; verdict: string; notes: string }[];
  recommendation: string;
  ambiguities: { term: string; quote: string; clarification: string }[];
  clarification_questions: string[];
}

export interface Analysis {
  result: ComplianceResult;
  source: "rule" | "ollama" | "gemini" | "template";
  fallback_reason: string;
}

export function specApi(rpc: RpcClient) {
  return {
    readFile: (path: string) => rpc.call<{ path: string; text: string; pdf: boolean; chars: number }>("spec.read_file", { path }),
    engines: () => rpc.call<{ ollama: boolean; ollama_models: string[]; gemini_key_in_env: boolean; gemini_models: string[] }>("spec.engines"),
    analyze: (p: { text: string; engine: "rule" | "ollama" | "gemini"; model?: string; api_key?: string; pdf_path?: string }, opts?: CallOptions) =>
      rpc.call<Analysis>("spec.analyze", p, { timeoutMs: 0, ...opts }),
    override: (text: string, row: MatrixRow, new_status: Status, note: string) =>
      rpc.call<{ logged: boolean }>("spec.override", { text, row, new_status, note }),
    templates: () => rpc.call<{ templates: string[] }>("spec.templates"),
    saveTemplate: (name: string, requirements: Requirement[]) => rpc.call<{ name: string }>("spec.template_save", { name, requirements }),
    runTemplate: (name: string) => rpc.call<Analysis>("spec.template_run", { name }),
    deleteTemplate: (name: string) => rpc.call<{ deleted: string }>("spec.template_delete", { name }),
    statement: (result: ComplianceResult, project_name: string, pdf_path?: string) =>
      rpc.call<{ markdown: string; pdf_path?: string }>("spec.statement", { result, project_name, pdf_path }),
    trainingExport: (path?: string) =>
      rpc.call<{ records: number; analyses: number; overrides: number; unique_specs: number; written?: number }>("training.export", path ? { path } : {}),
  };
}

export type SpecApi = ReturnType<typeof specApi>;
