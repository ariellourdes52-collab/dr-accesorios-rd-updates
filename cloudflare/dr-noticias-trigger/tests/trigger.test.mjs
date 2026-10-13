import test from "node:test";
import assert from "node:assert/strict";
import { runScheduled } from "../src/index.mjs";

const cron = "8 13-23,0-2 * * *";
const now = Date.UTC(2026, 9, 13, 0, 8); // 20:08 República Dominicana, 12/10
const event = {cron, scheduledTime: now};
const secret = "github_pat_SECURE_PRIVATE_SECRET_TEST_12345678901234567890";
const active = {DR_NOTICIAS_CF_ENABLED: "true", GH_WORKFLOW_DISPATCH_TOKEN: secret};

test("desactivado por defecto: no contacta GitHub", async () => {
  const result = await runScheduled(event, {}, () => {
    assert.fail("No debe acceder a GitHub");
  });
  assert.equal(result, "disabled");
});

test("rechaza un cron distinto u hora no programada", async () => {
  for (const controller of [
    {cron: "0 * * * *", scheduledTime: now},
    {cron, scheduledTime: Date.UTC(2026, 9, 12, 4, 8)},
    {cron, scheduledTime: Date.UTC(2026, 9, 13, 0, 9)},
    {cron, scheduledTime: NaN}
  ]) {
    assert.equal(await runScheduled(controller, active, () => assert.fail("no despachar")), "outside_schedule");
  }
});

test("llama una sola vez al workflow exacto de main, sin compartir secreto", async () => {
  const calls = [];
  const result = await runScheduled(event, active, async (url, opts) => {
    calls.push({url, opts});
    return {status: 204};
  });
  assert.equal(result, "dispatched");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url,
    "https://api.github.com/repos/ariellourdes52-collab/dr-accesorios-rd-updates/actions/workflows/dr-noticias-railway-dispatch.yml/dispatches");
  assert.equal(calls[0].opts.method, "POST");
  assert.equal(calls[0].opts.redirect, "manual");
  assert.equal(calls[0].opts.headers.Authorization, "Bearer " + secret);
  assert.equal(calls[0].opts.headers["X-GitHub-Api-Version"], "2022-11-28");
  assert.equal(calls[0].opts.body, '{"ref":"main"}');
  assert.ok(!calls[0].url.includes(secret));
  assert.ok(!calls[0].opts.body.includes(secret));
});

test("no ejecuta nada si falta secreto o está incompleto", async () => {
  const env = {DR_NOTICIAS_CF_ENABLED: "true"};
  await assert.rejects(runScheduled(event, env, async () => assert.fail()), /falta secreto/);
});

test("errores HTTP no filtran secreto y no realizan reintentos", async () => {
  let count = 0;
  await assert.rejects(
    runScheduled(event, active, async () => {
      count++;
      return {status: 401, body: secret};
    }),
    error => {
      assert.match(error.message, /HTTP 401/);
      assert.ok(!error.message.includes(secret));
      return true;
    }
  );
  assert.equal(count, 1);
});

test("fallos inciertos de red no exponen credenciales ni reintentan", async () => {
  let count = 0;
  await assert.rejects(
    runScheduled(event, active, async () => {
      count++;
      throw new Error("Authorization: Bearer " + secret);
    }),
    error => {
      assert.match(error.message, /fallo de red/);
      assert.ok(!error.message.includes(secret));
      return true;
    }
  );
  assert.equal(count, 1);
});

test("diagnostica rechazo de redireccion sin seguimiento ni reintentos", async () => {
  let count = 0;
  await assert.rejects(runScheduled(event, active, async (url, opts) => {
    count++;
    assert.equal(opts.redirect, "manual");
    return {status: 302};
  }), /HTTP 302/);
  assert.equal(count, 1);
});

test("diagnostica error DNS por categoria sin revelar el mensaje ni el token", async () => {
  const original = "DNS resolution failed Authorization: Bearer " + secret;
  await assert.rejects(runScheduled(event, active, async () => {
    throw Object.assign(new TypeError(original), {name: "TypeError"});
  }), error => {
    assert.match(error.message, /dns_resolution/);
    assert.ok(!error.message.includes(secret));
    assert.ok(!error.message.includes(original));
    return true;
  });
});

test("diagnostica TimeoutError sin retry", async () => {
  let count = 0;
  await assert.rejects(runScheduled(event, active, async () => {
    count++;
    throw Object.assign(new Error("Timed out"), {name: "TimeoutError"});
  }), /timeout_or_abort/);
  assert.equal(count, 1);
});

test("mensaje desconocido que contiene un secreto nunca se registra", async () => {
  await assert.rejects(runScheduled(event, active, async () => {
    throw new Error("Authorization: Bearer " + secret);
  }), error => {
    assert.match(error.message, /unknown_fetch_error/);
    assert.ok(!error.message.includes(secret));
    return true;
  });
});
