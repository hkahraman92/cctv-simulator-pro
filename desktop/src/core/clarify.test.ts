import { describe, expect, it } from "vitest";
import { uniqueQuestions } from "./clarify";

describe("uniqueQuestions", () => {
  it("drops a question that an ambiguity already shows", () => {
    const amb = [{ clarification: "Kaç MP isteniyor?" }];
    expect(uniqueQuestions(amb, ["Kaç MP isteniyor? ", "Hangi lens?"])).toEqual(["Hangi lens?"]);
  });
  it("returns an empty list when everything is shown already", () => {
    expect(uniqueQuestions([{ clarification: "A?" }], ["a?"])).toEqual([]);
  });
  it("keeps all questions when nothing matches and removes repeats", () => {
    expect(uniqueQuestions([], ["x", "x", "y"])).toEqual(["x", "y"]);
  });
});
