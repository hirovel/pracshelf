import {readFileSync} from 'node:fs';
// Build-time only; the public site receives rendered HTML, never the private DB/API.
export const collection=JSON.parse(readFileSync(process.env.READING_MANIFEST,'utf8'));
export const dateLabel=value=>value?value.slice(0,10):'未知';
