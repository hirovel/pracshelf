import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
execFileSync(process.execPath,[fileURLToPath(new URL('../node_modules/astro/bin/astro.mjs',import.meta.url)),'build'],{
 cwd:fileURLToPath(new URL('../',import.meta.url)), env:{...process.env,ASTRO_TELEMETRY_DISABLED:'1'},stdio:'inherit',windowsHide:true
});
const {publishCompiledPages}=await import('./publish.mjs');
await publishCompiledPages(fileURLToPath(new URL('../../',import.meta.url)));
console.log('Published compiled pages and notices.');
