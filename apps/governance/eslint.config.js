import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist", "src/**/*.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs["flat/essential"],
  {
    languageOptions: {
      globals: {
        crypto: "readonly",
      },
    },
  },
  {
    files: ["**/*.vue"],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
];
