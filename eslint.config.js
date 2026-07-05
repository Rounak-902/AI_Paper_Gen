import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // Allow context files and shadcn/ui to export hooks/variants alongside components
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Data-fetching on mount via useEffect is a standard React pattern
      'react-hooks/set-state-in-effect': 'warn',
      // Mutable render vars are fine when used as precomputed values
      'react-hooks/immutability': 'warn',
    },
  },
])
