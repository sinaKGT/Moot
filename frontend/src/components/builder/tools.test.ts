import { describe, expect, it } from "vitest";
import { parseEnv } from "./ToolsDialog";

describe("adding an MCP server", () => {
  it("reads environment variables written as KEY=value lines", () => {
    expect(parseEnv("TOKEN=abc=def\n\n  REGION = eu \nnot a variable")).toEqual({ TOKEN: "abc=def", REGION: "eu" });
  });
});
