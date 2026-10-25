import assert from "node:assert/strict";
import test from "node:test";
import { validateDeploymentApiBase } from "./validate-deployment-env.mjs";

test("deployment refuses missing and local API targets", () => {
  for (const value of [undefined, "", "http://127.0.0.1:8000", "https://localhost", "https://localhost.", "https://sub.localhost.", "https://127.0.0.1", "https://[::1]", "https://0.0.0.0"]) {
    assert.throws(() => validateDeploymentApiBase(value));
  }
});

test("deployment refuses credentials and endpoint suffixes", () => {
  for (const value of ["https://user:secret@api.example.com", "https://api.example.com/draft", "https://api.example.com?token=x", "https://api.example.com#x", " https://api.example.com"]) {
    assert.throws(() => validateDeploymentApiBase(value));
  }
});

test("deployment accepts an explicitly configured HTTPS origin", () => {
  assert.equal(validateDeploymentApiBase("https://api.example.com/"), "https://api.example.com");
});
