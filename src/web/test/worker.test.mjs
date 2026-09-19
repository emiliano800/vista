import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker.mjs';

test('serves assets with a restrictive CSP and proxies only the report surface',async () => {
  const result = await worker.fetch(new Request('https://bumpsolutions.org/'),{ASSETS:{fetch:async () => new Response('Vista')}});
  assert.equal(await result.text(),'Vista'); assert.match(result.headers.get('content-security-policy'),/frame-ancestors 'none'/);
  assert.equal((await worker.fetch(new Request('https://bumpsolutions.org/api/tenants'),{})).status,404);
  assert.equal((await worker.fetch(new Request('https://bumpsolutions.org/api/deals',{method:'POST'}),{})).status,405);
  assert.equal((await worker.fetch(new Request('https://bumpsolutions.org/api/auth/me'),{})).status,503);
});
test('proxy forwards sessions, preserves cookies, never caches data, rejects cross-origin writes',async () => {
  const original = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (url, options) => {
    forwarded = {url,options};
    return new Response('{}',{headers:{'Set-Cookie':'vista_session=opaque; Secure; HttpOnly; SameSite=Lax; Path=/'}});
  };
  try {
    const env = {API_ORIGIN:'https://api.example.com'};
    const request = new Request('https://bumpsolutions.org/api/auth/session',{method:'POST',headers:{origin:'https://bumpsolutions.org','X-Vista-Request':'1','Content-Type':'application/json',cookie:'vista_session=old','x-untrusted':'no'},body:'{}'});
    const response = await worker.fetch(request,env);
    assert.equal(forwarded.url.href,'https://api.example.com/api/auth/session');
    assert.equal(forwarded.options.headers.get('cookie'),'vista_session=old');
    assert.equal(forwarded.options.headers.get('x-untrusted'),null);
    assert.equal(forwarded.options.redirect,'manual');
    assert.match(response.headers.get('set-cookie'),/HttpOnly/);
    assert.equal(response.headers.get('cache-control'),'no-store');
    assert.equal((await worker.fetch(new Request('https://bumpsolutions.org/api/auth/session',{method:'POST',headers:{origin:'https://evil.example'}}),env)).status,403);
    globalThis.fetch = async () => new Response(null,{status:302,headers:{location:'https://other.example'}});
    assert.equal((await worker.fetch(new Request('https://bumpsolutions.org/api/auth/me'),env)).status,502);
  } finally { globalThis.fetch = original; }
});
