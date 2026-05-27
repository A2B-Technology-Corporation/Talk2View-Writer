// Webpack's `asset/source` loader returns the file's contents as a
// string. Tell TypeScript so ``import md from './foo.md'`` typechecks.
declare module '*.md' {
  const content: string;
  export default content;
}

// Injected by webpack's DefinePlugin (see webpack.config.js) from
// src/web/package.json's version — the bundled extension version.
declare const __APP_VERSION__: string;
