import { describe, expect, it } from "vitest";
import type { KnowledgeDocument } from "../../types";
import { documentStatus } from "./KnowledgePanel";
import { looksLikeEmbedding } from "./ModelPicker";

const doc = (patch: Partial<KnowledgeDocument>): KnowledgeDocument => ({
  id: "d",
  filename: "a.pdf",
  bytes: 10,
  created_at: "",
  status: "indexing",
  stage: "",
  chunks_total: 0,
  chunks_mapped: 0,
  chunks_failed: 0,
  error: null,
  notes: [],
  ...patch,
});

describe("an agent's files", () => {
  it("say in words how far indexing has got", () => {
    expect(documentStatus(doc({ status: "queued" }))).toBe("Waiting…");
    expect(documentStatus(doc({ stage: "reading" }))).toBe("Reading…");
    expect(documentStatus(doc({ stage: "mapping", chunks_total: 40, chunks_mapped: 11, chunks_failed: 1 }))).toBe("Mapping into the graph: 12 of 40");
    expect(documentStatus(doc({ stage: "mapping", chunks_total: 900 }))).toBe("Mapping into the graph: 0 of 400");
    expect(documentStatus(doc({ status: "ready", chunks_total: 1 }))).toBe("Ready · 1 passage");
    expect(documentStatus(doc({ status: "failed", error: "a.pdf is password-protected." }))).toBe("a.pdf is password-protected.");
  });

  it("put embedding models first", () => {
    expect(["text-embedding-nomic-embed-text-v1.5", "bge-m3", "qwen3-8b"].map(looksLikeEmbedding)).toEqual([true, true, false]);
  });
});
