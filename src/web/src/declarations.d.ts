// Webpack's `asset/source` loader returns the file's contents as a
// string. Tell TypeScript so ``import md from './foo.md'`` typechecks.
declare module '*.md' {
  const content: string;
  export default content;
}
