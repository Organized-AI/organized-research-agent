import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, isAbsolute, relative, resolve } from 'node:path';

const root = process.cwd();
const contentType = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
const server = http.createServer(async (req, res) => {
  const requested = decodeURIComponent(req.url === '/' ? '/index.html' : req.url.split('?')[0]);
  const file = resolve(root, `.${requested}`);
  const outsideRoot = relative(root, file);
  if (outsideRoot.startsWith('..') || isAbsolute(outsideRoot)) return res.writeHead(403).end();
  try {
    let body = await readFile(file);
    if (extname(file) === '.html') body = await renderSsi(body.toString());
    res.writeHead(200, { 'content-type': contentType[extname(file)] ?? 'text/plain', 'cache-control': 'no-store' }).end(body);
  }
  catch { res.writeHead(404).end('Not found'); }
});

async function renderSsi(html) {
  const directive = /<!--#\s*include\s+virtual="([^"]+)"\s*-->/g;
  const includes = await Promise.all([...html.matchAll(directive)].map(async match => {
    const virtualPath = decodeURIComponent(match[1]);
    const includeFile = resolve(root, `.${virtualPath}`);
    const outsideRoot = relative(root, includeFile);
    if (outsideRoot.startsWith('..') || isAbsolute(outsideRoot)) throw new Error('unsafe SSI include');
    return [match[0], await readFile(includeFile, 'utf8')];
  }));
  return includes.reduce((page, [token, content]) => page.replace(token, content), html);
}
server.listen(4173, '127.0.0.1', () => console.log('http://127.0.0.1:4173'));
