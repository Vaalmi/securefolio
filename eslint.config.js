import js from "@eslint/js";
import vitest from "@vitest/eslint-plugin";
import prettier from "eslint-config-prettier";
import { defineConfig } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import playwright from "eslint-plugin-playwright";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import sonarjs from "eslint-plugin-sonarjs";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  { ignores: ["**/dist", "**/coverage", "e2e/test-results"] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  sonarjs.configs.recommended,
  {
    linterOptions: { reportUnusedDisableDirectives: "error" },
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      complexity: ["error", 15],
      "max-depth": ["error", 4],
      "max-lines": [
        "error",
        { max: 500, skipBlankLines: true, skipComments: true },
      ],
      "max-lines-per-function": [
        "error",
        { max: 200, skipBlankLines: true, skipComments: true },
      ],
      "max-params": ["error", 5],
    },
  },
  {
    files: ["**/*.js", "**/*.mjs"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
  {
    // The repo's own tools run git and other programs, which live wherever each
    // machine installed them, so they are found on PATH.
    files: ["scripts/**", "e2e/**/*.mjs"],
    rules: { "sonarjs/no-os-command-from-path": "off" },
  },
  {
    files: ["app/src/**/*.{ts,tsx}"],
    extends: [
      reactHooks.configs.flat["recommended-latest"],
      jsxA11y.configs.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: { globals: globals.browser },
  },
  {
    // api.ts is the app's one way to the backend: no other module builds a request.
    files: ["app/src/**/*.{ts,tsx}"],
    ignores: [
      "app/src/api.ts",
      "app/src/connection.ts",
      "app/src/**/*.test.{ts,tsx}",
      "app/src/setupTests.ts",
    ],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              regex: "(^|/)connection(\\.[cm]?[jt]sx?)?$",
              message: "Reach the backend through api.ts.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["app/src/**/*.test.{ts,tsx}"],
    extends: [vitest.configs.recommended],
    // The tests' addresses are stand-ins, and the real one is loopback.
    rules: { "sonarjs/no-clear-text-protocols": "off" },
  },
  {
    files: ["e2e/**/*.ts"],
    extends: [playwright.configs["flat/recommended"]],
  },
  prettier,
);
