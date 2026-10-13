import {cp,mkdir} from 'node:fs/promises';
const target=new URL('../../src/learning_library/cactus/',import.meta.url);
await mkdir(target,{recursive:true});
await cp(new URL('../dist/',import.meta.url),target,{recursive:true});
await cp(new URL('../LICENSE',import.meta.url),new URL('LICENSE',target));
await cp(new URL('../UPSTREAM.md',import.meta.url),new URL('UPSTREAM.md',target));
await cp(new URL('../licenses/',import.meta.url),new URL('licenses/',target),{recursive:true});
console.log('Published compiled Cactus pages and assets.');
