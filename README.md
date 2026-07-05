# AI Question Paper Generator

## Math Rendering (MANDATORY RULE)

All question-related text fields — `question_text`, `answer_text`, `answer_hint`, `options`, `solution_steps`, `assertion`, `reason`, `blank_answer`, `correct_option` — **MUST** be rendered using the `<MathRenderer>` component (located at `src/components/shared/MathRenderer.tsx`), not as raw `{value}` strings.

This rule applies to:
- Every existing screen that displays question content
- **Every future screen** that will display question content (public links, mobile views, print routes, export features, student portals, etc.)

### How it works
- Gemini prompts instruct the AI to wrap all mathematical expressions in `$...$` (inline) or `$$...$$` (display) LaTeX syntax
- `MathRenderer` parses the mixed text+LaTeX string and renders math segments via KaTeX
- Admins typing questions manually can use the `$...$` syntax and see a live preview in the admin panel
- KaTeX CSS is imported globally in `main.tsx`

### Quick reference for writing LaTeX in questions
| Syntax | Renders as |
|--------|-----------|
| `$x^2$` | x² |
| `$\frac{d}{dx}$` | d/dx fraction |
| `$\sqrt{x+1}$` | √(x+1) |
| `$\sin(x)$` | sin(x) upright |
| `$x_1$` | x₁ subscript |

---

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
