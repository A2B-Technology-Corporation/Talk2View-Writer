const path = require('path');
const webpack = require('webpack');
const HtmlWebpackPlugin = require('html-webpack-plugin');
// Single source of truth for the bundled version — bumped by the
// release version dance alongside pyproject.toml / __init__.py.
const PKG_VERSION = require('./package.json').version;

/**
 * Webpack config for the Talk2View-Writer chat UI.
 *
 * Inputs:  src/web/src/index.tsx + src/web/index.html
 * Output:  src/web/dist/index.html + bundle.js
 *
 * The Makefile copies src/web/dist/ into build/Talk2ViewWriter/web/
 * during `make build`, so the .oxt ships the bundled HTML + JS in
 * its web/ folder. The pywebview subprocess loads the HTML via
 * file:// URL.
 */
module.exports = (env, argv) => {
  const mode = argv && argv.mode ? argv.mode : 'production';
  return {
    mode,
    entry: './src/index.tsx',
    devtool: mode === 'production' ? false : 'inline-source-map',
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: 'bundle.js',
      clean: true,
    },
    resolve: {
      extensions: ['.tsx', '.ts', '.js'],
    },
    module: {
      rules: [
        {
          // @talk2view/sdk ships ESM with extensionless imports.
          // Webpack 5's strict ESM resolver demands extensions on
          // .js imports inside .mjs/.js modules — relax that for
          // node_modules.
          test: /\.m?js$/,
          resolve: { fullySpecified: false },
        },
        {
          test: /\.tsx?$/,
          loader: 'ts-loader',
          exclude: /node_modules/,
          options: { transpileOnly: false },
        },
        {
          // Inline .md files (notably SYSTEM_PROMPT.md) as raw strings.
          // App.tsx imports the Writer system prompt this way and
          // passes it to the SDK's <Talk2View systemPrompt={...}>
          // prop so the engine sees the Writer-flavoured prompt even
          // when the partner key it's using has a different default
          // configured.
          test: /\.md$/,
          type: 'asset/source',
        },
      ],
    },
    plugins: [
      // Inline the extension version so the in-app update banner can
      // compare it against the latest GitHub release.
      new webpack.DefinePlugin({
        __APP_VERSION__: JSON.stringify(PKG_VERSION),
      }),
      new HtmlWebpackPlugin({
        template: path.resolve(__dirname, 'index.html'),
        filename: 'index.html',
        inject: 'body',
      }),
    ],
    performance: {
      // The Talk2View SDK + React weighs in around 1 MB minified.
      // We're shipping a desktop extension, not a website — disable
      // the noisy warning about asset size.
      hints: false,
    },
  };
};
