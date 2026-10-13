import {defineConfig} from 'astro/config';
import {fileURLToPath} from 'node:url';
if(!process.env.READING_OUTPUT || !process.env.READING_MANIFEST) throw new Error('请使用 npm run reading:build。');
export default defineConfig({
  root:fileURLToPath(new URL('./',import.meta.url)),
  srcDir:fileURLToPath(new URL('./src/',import.meta.url)),
  publicDir:fileURLToPath(new URL('./public/',import.meta.url)),
  cacheDir:fileURLToPath(new URL('./.astro/',import.meta.url)),
  outDir:process.env.READING_OUTPUT,output:'static',
  devToolbar:{enabled:false},build:{format:'directory',inlineStylesheets:'always'},
});
